"""Read-only explanations derived from immutable forecasts and recorded trade events.

No decision, balance, order or forecast is changed by this module.
"""
from services.evidence_quality import summarize_evidence, utc
import pandas as pd


def forecast_evidence(snapshot):
    news = (snapshot or {}).get("focus", {}).get("sources", {}).get("news", {})
    direct = news.get("sentiment_result", {})
    inputs = news.get("sentiment_inputs", {})
    direct_quality = direct.get("evidence_quality")
    if not direct_quality and inputs.get("observed_at"):
        direct_quality = summarize_evidence(inputs.get("news", []), inputs.get("social", []),
                                            observed_at=inputs["observed_at"])
    if direct.get("error"):
        direct_quality = {"status": "unavailable", "reason": direct["error"]}
    driver_quality = news.get("driver_quality")
    if not driver_quality and "driver_news" in news and news.get("observed_at"):
        driver_quality = summarize_evidence(news["driver_news"], observed_at=news["observed_at"])
    return {"direct_sentiment": direct_quality or {"status": "not_recorded"},
            "driver_news": driver_quality or {"status": "not_recorded"},
            "basis": "inputs saved at forecast issuance; not current news",
            "affects_trading_policy": False}


REASONS = {
    "view_invalidated": "Two completed five-minute closes fell below session VWAP; exited at the following minute open.",
    "stop": "The recorded one-minute path reached the protective stop (including worse opening gaps).",
    "target": "The recorded one-minute path reached the committed profit target.",
    "time_exit": "The four-hour observation window ended.",
    "daily_loss": "Observed equity reached the daily loss trigger.",
}


def explain_trade(payload, view=None, forecast=None):
    """Amounts reconcile to the stored fill ledger; no prices are estimated here."""
    p = payload
    quantity, entry = p["quantity"], p["entry_raw_price"]
    closed = "exit_raw_price" in p
    gross = quantity*(p["exit_raw_price"]-entry) if closed else None
    costs = p.get("total_cost") if closed else p["entry_cost"]
    net = p.get("net_pnl") if closed else None
    explanation = {
        "entry_key": p["entry_key"], "symbol": p["symbol"], "entry_at": p["entry_at"],
        "quantity": quantity, "entry_raw_price": entry, "entry_modeled_price": p["entry_price"],
        "stop": p["stop"], "target": p["target"], "planned_loss_usd": p["planned_loss"],
        "entry_reason": ("A new five-minute close broke the first 15-minute high while above session VWAP and the session open."
                         if view and view.get("eligible") else "Original qualifying signal not available in this report."),
        "signal": {k: view.get(k) for k in ("bar_end", "close", "vwap", "opening_range_high", "opening_range_low", "reason")} if view else None,
        "gross_price_pnl_usd": gross, "modeled_costs_usd": costs, "net_pnl_usd": net,
        "accounting_reconciles": abs(gross-costs-net) < 1e-8 if closed else None,
        "exit_raw_price": p.get("exit_raw_price"), "exit_reason": REASONS.get(p.get("reason"), p.get("reason")),
        "ambiguous_stop_target": p.get("ambiguous_stop_target", False),
        "loss_attribution": ("price_and_costs" if gross < 0 else "costs_exceeded_price_gain") if closed and net < 0 else None,
        "forecast_evidence": forecast_evidence(forecast.input_snapshot) if forecast else {"status": "not_recorded"},
    }
    if forecast and forecast.reference_open and forecast.direction == "UP":
        upper = forecast.reference_open*(1+forecast.move_high_pct/100)
        explanation["forecast_alignment"] = {
            "upper_excursion_price": upper,
            "remaining_to_upper_pct_from_entry": (upper/entry-1)*100,
            "target_above_upper_band": p["target"] > upper,
            "meaning": "Historical excursion IQR, not a price ceiling or calibrated target probability.",
            "affects_trading_policy": False}
    else:
        explanation["forecast_alignment"] = {"status": "unavailable", "reason": "No verified session open and UP forecast in this report."}
    return explanation


def path_excursions(payload, exit_at, source):
    """Use only the hash-verified recorded minute path, never a later price fetch."""
    if not source:
        return {"status": "unverified", "reason": "Recorded minute source unavailable."}
    from services.intraday_market import load_archived_bars
    try:
        if source.get("interval_minutes") != 1:
            raise ValueError("minute_interval_required")
        bars = load_archived_bars(source)
        start, end = pd.Timestamp(utc(payload["entry_at"])), pd.Timestamp(utc(exit_at))
        window = bars[(bars.index >= start) & (bars.index < end)]
        expected = pd.date_range(start, end, freq="1min", inclusive="left")
        if not window.index.tz_convert("UTC").equals(expected):
            raise ValueError("incomplete_recorded_trade_path")
        ambiguous = payload.get("reason") in {"stop", "target"}
        # OHLC cannot locate extrema relative to an intrabar stop/target fill.
        measured = window.iloc[:-1] if ambiguous else window
        entry, exit_price = payload["entry_raw_price"], payload["exit_raw_price"]
        highest = max(entry, exit_price, float(measured.high.max()) if len(measured) else entry)
        lowest = min(entry, exit_price, float(measured.low.min()) if len(measured) else entry)
        return {"status": "lower_bounds_intrabar_exit" if ambiguous else "verified",
                "mfe_pct": (highest/entry-1)*100, "mae_pct": (1-lowest/entry)*100,
                "bars_verified": len(window), "cost_basis": "raw prices before costs",
                "source_sha256": source["sha256"],
                "note": "Exit-minute extrema excluded when their timing relative to the fill is unknown."}
    except (ValueError, KeyError, OSError) as exc:
        return {"status": "unverified", "reason": str(exc)}


def account_diagnostics(state, exits, views, forecasts, minute_sources=None):
    signals = {f"{v.symbol}:{v.payload['bar_end']}": v.payload for v in views}
    trades = [{**explain_trade(r.payload, signals.get(r.payload["entry_key"]), forecasts.get(r.payload.get("forecast_id"))),
               "exit_at": utc(r.occurred_at).isoformat(),
               "excursions": path_excursions(r.payload, r.occurred_at, (minute_sources or {}).get(r.id)),
               "session_date": r.payload["entry_at"][:10]} for r in exits]
    current = [t for t in trades if t["session_date"] == state["day"]]
    return {"scope": "recorded closed trades; pending orders and positions remain in account state",
            "trades": trades, "session": {
                "session_date": state["day"], "starting_equity": state["day_start_equity"],
                "cash_benchmark_equity": state["day_start_equity"],
                "equity_change_usd": state["equity"]-state["day_start_equity"],
                "closed_trades": len(current),
                "gross_price_pnl_usd": sum(t["gross_price_pnl_usd"] for t in current),
                "closed_trade_costs_usd": sum(t["modeled_costs_usd"] for t in current),
                "closed_trade_net_pnl_usd": sum(t["net_pnl_usd"] for t in current),
                "unresolved": bool(state["position"] or state["pending"] or state["data_uncertain"]),
                "market_data_errors": state.get("market_data_errors", []),
            }}
