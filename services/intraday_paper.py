"""Long-only $100 experiment. No order broker is imported or called by this module.

The same deterministic transition runs on live observations and saved replay inputs.
Pending entries fill at the next minute open AFTER submission. Existing positions
are managed from minute OHLC, with stop-first ordering when a bar is ambiguous.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, time
import math
from threading import Lock
import hashlib
from pathlib import Path

import pandas as pd
from sqlalchemy.exc import IntegrityError

from core.database import SessionLocal, PaperExperimentAccount, PaperExperimentEvent, IntradayForecast
from services.intraday_market import (NY, UTC, FutuIntradayBars, complete_window,
                                      normalize_bars, session_window, load_archived_bars)
from services.intraday_forecast import FOCUS_SYMBOLS, EXPERIMENT, STRATEGY_VERSION
from services.notifications import enqueue_in_session
from services.paper_diagnostics import account_diagnostics, REASONS

POLICY_VERSION = "orb-vwap-long-v1"
ACCOUNT_ID = "etf-paper-100"
COMPARATOR_ID = "etf-paper-100-no-forecast"


def implementation_digest():
    root = Path(__file__).resolve().parent
    return hashlib.sha256(b"".join((root/name).read_bytes() for name in
        ("intraday_paper.py", "intraday_market.py", "intraday_forecast.py"))).hexdigest()


@dataclass(frozen=True)
class PaperConfig:
    starting_equity: float = 100.0
    risk_per_trade: float = 0.25
    daily_loss_limit: float = 1.0
    max_entries: int = 4
    friction_bps_per_side: float = 5.0
    minimum_notional: float = 5.0
    pending_seconds: int = 180
    require_daily_up: bool = True


def initial_state(config):
    return {"cash": config.starting_equity, "equity": config.starting_equity,
            "peak_equity": config.starting_equity, "max_drawdown": 0.0,
            "costs": 0.0, "realized_pnl": 0.0, "position": None, "pending": None,
            "day": None, "day_start_equity": config.starting_equity,
            "entries_today": 0, "halted": False, "last_signals": {},
            "closed_trades": 0, "wins": 0, "gross_profit": 0.0, "gross_loss": 0.0,
            "data_uncertain": False, "last_observed_at": None}


def _instant(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("timezone_required")
    return stamp.to_pydatetime().astimezone(NY)


def entry_signal(symbol, frame, forecast, now, config):
    """A frozen hypothesis, not a calibrated probability: opening-range breakout + VWAP."""
    start, end = session_window(now.date())
    latest_end = pd.Timestamp(now).floor("5min").to_pydatetime()
    if latest_end <= start:
        return None
    window = complete_window(frame, now.date(), end=min(latest_end, end))
    if "volume" not in window or float(window.volume.sum()) <= 0:
        raise ValueError("missing_session_volume")
    typical = (window.high + window.low + window.close) / 3
    vwap = float((typical * window.volume).sum() / window.volume.sum())
    last = window.iloc[-1]
    opening = float(window.iloc[0].open)
    view = "UP" if last.close > vwap and last.close > opening else "DOWN"
    opening_range = window.iloc[:3]
    high, low = float(opening_range.high.max()), float(opening_range.low.min())
    detail = {"symbol": symbol, "bar_end": latest_end.isoformat(), "view": view,
              "close": float(last.close), "vwap": vwap, "opening_range_high": high,
              "opening_range_low": low, "forecast_id": forecast.get("id") if forecast else None,
              "source": frame.attrs.get("provenance"), "eligible": False}
    if not forecast or forecast.get("strategy_version") != STRATEGY_VERSION:
        detail["reason"] = "missing_current_version_premarket_forecast"
    elif _instant(forecast["issued_at"]) >= start:
        detail["reason"] = "late_forecast"
    elif config.require_daily_up and forecast["direction"] != "UP":
        detail["reason"] = "premarket_down_stay_out"
    elif not start + timedelta(minutes=20) <= latest_end or now.time() >= time(13):
        detail["reason"] = "outside_entry_window"
    elif not (float(window.iloc[-2].close) <= high < float(last.close) and view == "UP"):
        detail["reason"] = "no_new_opening_range_breakout"
    else:
        detail.update(eligible=True, reason="opening_range_breakout_above_session_vwap", stop=low)
    # Two completed closes under their current cumulative VWAP invalidate an open long.
    cumulative = (typical * window.volume).cumsum() / window.volume.cumsum()
    detail["invalidate_long"] = len(window) >= 2 and bool((window.close.iloc[-2:] < cumulative.iloc[-2:]).all())
    return detail


def transition(state, now, minute_frames, views, config):
    """Return new state and events without DB, network, wall clock or notifications."""
    now = _instant(now)
    state = deepcopy(state)
    events = []
    friction = config.friction_bps_per_side / 10000

    def emit(kind, event_key, event_symbol=None, event_at=None, **payload):
        events.append({"kind": kind, "event_key": event_key, "symbol": event_symbol,
                       "occurred_at": (event_at or now).astimezone(UTC).isoformat(), "payload": payload})

    def mark(raw):
        p = state["position"]
        state["equity"] = state["cash"] + (p["quantity"] * raw * (1-friction) if p else 0)
        state["peak_equity"] = max(state["peak_equity"], state["equity"])
        state["max_drawdown"] = max(state["max_drawdown"], state["peak_equity"]-state["equity"])

    def close(raw, at, reason, uncertain=False):
        p = state["position"]
        fill = raw * (1-friction)
        proceeds = p["quantity"] * fill
        pnl = proceeds - p["entry_debit"]
        cost = p["quantity"] * raw * friction
        state["cash"] += proceeds
        state["costs"] += cost
        state["realized_pnl"] += pnl
        state["closed_trades"] += 1
        state["wins"] += int(pnl > 0)
        state["gross_profit"] += max(pnl, 0)
        state["gross_loss"] += max(-pnl, 0)
        emit("EXIT", f"exit:{p['entry_key']}", p["symbol"], at, **p,
             exit_price=fill, exit_raw_price=raw, net_pnl=pnl, exit_cost=cost,
             total_cost=p["entry_cost"]+cost, reason=reason, ambiguous_stop_target=uncertain)
        state["position"] = None
        mark(0)
        if state["equity"] <= state["day_start_equity"]-config.daily_loss_limit:
            state["halted"] = True

    # A day with an unresolved position cannot silently reset its loss limits.
    if state["day"] != now.date().isoformat() and not state["position"] and not state["pending"]:
        state.update(day=now.date().isoformat(), day_start_equity=state["cash"], entries_today=0,
                     halted=False, pending=None, last_signals={})
        emit("SESSION", f"session:{state['day']}", starting_equity=state["cash"])
    state["data_uncertain"] = False
    pending = state["pending"]
    if pending:
        submitted = _instant(pending["submitted_at"])
        intended_fill = (pd.Timestamp(submitted).floor("min")+pd.Timedelta(minutes=1)).to_pydatetime()
        if (intended_fill > submitted+timedelta(seconds=config.pending_seconds)
                or intended_fill.time() >= time(13) or state["halted"]):
            emit("CANCEL", f"cancel:{pending['key']}", pending["symbol"], reason="expired_or_entry_cutoff")
            state["pending"] = pending = None
        # A previously submitted order may already have filled during downtime.
        # A late observation must verify that path; it cannot erase the order.

    active = state["position"] or state["pending"]
    if active:
        symbol = active["symbol"]
        frame = normalize_bars(minute_frames.get(symbol), interval_minutes=1)
        day = _instant(active.get("entry_at", active.get("submitted_at"))).date()
        _, deadline = session_window(day)
        frame = frame[(frame.index + pd.Timedelta(minutes=1) <= now) & (frame.index < deadline)]
        position = state["position"]
        if position:
            expected_start = _instant(position["processed_until"])
        else:
            expected_start = (pd.Timestamp(_instant(active["submitted_at"])).floor("min")
                              + pd.Timedelta(minutes=1)).to_pydatetime()
        available_end = min(pd.Timestamp(now).floor("min").to_pydatetime(), deadline)
        needed = (pd.date_range(expected_start, available_end, freq="1min", inclusive="left")
                  if available_end > expected_start else pd.DatetimeIndex([], tz=NY))
        usable = frame[(frame.index >= expected_start) & (frame.index < available_end)]
        if not usable.index.equals(needed):
            state["data_uncertain"] = True
            emit("DATA_GAP", f"gap:{now.isoformat()}:{symbol}", symbol,
                 expected=len(needed), observed=len(usable), reason="position_path_unverifiable")
        else:
            for stamp, bar in usable.iterrows():
                bar_start = stamp.to_pydatetime()
                bar_end = bar_start+timedelta(minutes=1)
                if state["pending"]:
                    order = state["pending"]
                    raw, stop = float(bar.open), order["stop"]
                    entry = raw * (1+friction)
                    loss_per_share = entry-stop*(1-friction)
                    target = order["target"]
                    reward = target*(1-friction)-entry
                    qty = order["quantity"]
                    if (raw <= stop or raw > order["limit_price"] or qty*raw < config.minimum_notional
                            or reward < 1.2*loss_per_share or qty*entry > state["cash"]):
                        emit("CANCEL", f"cancel:{order['key']}", symbol, bar_start, reason="risk_cost_or_minimum_size")
                        state["pending"] = None
                        break
                    debit = qty*entry
                    position = {"symbol": symbol, "quantity": qty, "entry_at": bar_start.isoformat(),
                                "entry_price": entry, "entry_raw_price": raw, "entry_debit": debit,
                                "entry_cost": qty*raw*friction, "stop": stop, "target": target,
                                "entry_key": order["key"], "forecast_id": order["forecast_id"],
                                "processed_until": bar_start.isoformat(), "exit_requested_at": None,
                                "planned_loss": qty*loss_per_share}
                    state["position"], state["pending"] = position, None
                    state["cash"] -= debit
                    state["costs"] += position["entry_cost"]
                    state["entries_today"] += 1
                    emit("ENTRY", f"entry:{order['key']}", symbol, bar_start, **position)
                p = state["position"]
                if p is None:
                    break
                stop_hit, target_hit = float(bar.low) <= p["stop"], float(bar.high) >= p["target"]
                # Stop-first is conservative when intrabar ordering is unknown.
                if p.get("exit_requested_at") and bar_start > _instant(p["exit_requested_at"]):
                    close(float(bar.open), bar_start, "view_invalidated")
                elif stop_hit:
                    close(min(float(bar.open), p["stop"]), bar_end, "stop", target_hit)
                elif target_hit:
                    close(p["target"], bar_end, "target")
                elif bar_end >= deadline:
                    close(float(bar.close), bar_end, "time_exit")
                else:
                    p["processed_until"] = bar_end.isoformat()
                    p["last_mark"] = float(bar.close)
                    mark(float(bar.close))
                    if state["equity"] <= state["day_start_equity"]-config.daily_loss_limit:
                        state["halted"] = True
                        close(float(bar.close), bar_end, "daily_loss")
                if state["position"] is None:
                    break

    # Views are recorded at observation time, never backdated to the signal bar.
    for view in views:
        symbol = view["symbol"]
        if state["last_signals"].get(symbol) == view["bar_end"]:
            continue
        state["last_signals"][symbol] = view["bar_end"]
        emit("VIEW", f"view:{symbol}:{view['bar_end']}", symbol, **view)
        p = state["position"]
        if p and p["symbol"] == symbol and view.get("invalidate_long") and not p["exit_requested_at"]:
            p["exit_requested_at"] = now.isoformat()
            emit("EXIT_REQUEST", f"exit-request:{p['entry_key']}", symbol, reason="two_closes_below_session_vwap")
        if (view["eligible"] and not state["position"] and not state["pending"] and not state["halted"]
                and not state["data_uncertain"] and state["entries_today"] < config.max_entries
                and now.date().isoformat() == state["day"] and now.time() < time(13)
                and timedelta(0) <= now-_instant(view["bar_end"]) <= timedelta(seconds=90)):
            key = f"{symbol}:{view['bar_end']}"
            raw, stop = view["close"], view["stop"]
            entry = raw * (1+friction)
            loss = entry-stop*(1-friction)
            target = raw+2*(raw-stop)
            qty = math.floor(min(state["cash"]/entry, config.risk_per_trade/loss)*10000)/10000 if loss > 0 else 0
            if (raw <= stop or qty*raw < config.minimum_notional
                    or target*(1-friction)-entry < 1.2*loss):
                emit("SKIP", f"skip:{key}", symbol, reason="risk_cost_or_minimum_size")
                continue
            # Freeze size and prices BEFORE the fill; a gap above the committed
            # ceiling cancels this one-shot next-minute limit-order assumption.
            ceiling = min(state["cash"]/qty, config.risk_per_trade/qty+stop*(1-friction))/(1+friction)
            state["pending"] = {"key": key, "symbol": symbol, "submitted_at": now.isoformat(),
                                "stop": stop, "target": target, "quantity": qty, "limit_price": ceiling,
                                "forecast_id": view["forecast_id"]}
            emit("ORDER", f"order:{key}", symbol, **state["pending"])
    state["last_observed_at"] = now.isoformat()
    # Monetary state keeps sub-cent precision internally; displayed reports round only.
    assert state["cash"] >= -1e-8, "negative paper cash"
    return state, events


class IntradayPaperService:
    def __init__(self, bars=None, session_factory=SessionLocal, clock=None):
        self.bars = bars or FutuIntradayBars()
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(NY))
        self.lock = Lock()
        self.implementation_sha256 = implementation_digest()

    def bootstrap(self):
        for account_id, filtered in ((ACCOUNT_ID, True), (COMPARATOR_ID, False)):
            config = PaperConfig(require_daily_up=filtered)
            with self.session_factory() as db:
                if db.get(PaperExperimentAccount, account_id) is None:
                    db.add(PaperExperimentAccount(id=account_id, policy_version=POLICY_VERSION,
                           config=asdict(config), state=initial_state(config)))
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()

    def tick(self):
        if not self.lock.acquire(blocking=False):
            return {"status": "already_running"}
        try:
            return self._tick()
        finally:
            self.lock.release()

    def _tick(self):
        self.bootstrap()
        now = self.clock().astimezone(NY)
        with self.session_factory() as db:
            accounts = db.query(PaperExperimentAccount).filter(PaperExperimentAccount.id.in_([ACCOUNT_ID, COMPARATOR_ID])).all()
            states = {a.id: deepcopy(a.state) for a in accounts}
            rows = db.query(IntradayForecast).filter_by(experiment=EXPERIMENT,
                strategy_version=STRATEGY_VERSION, session_date=now.date()).all()
            forecasts = {r.symbol: {"id": r.id, "direction": r.direction,
                "strategy_version": r.strategy_version,
                "issued_at": r.issued_at.replace(tzinfo=UTC).isoformat()} for r in rows}
        try:
            start, end = session_window(now.date())
            observe = start+timedelta(minutes=5) <= now <= end+timedelta(minutes=2)
        except ValueError:
            observe = False
        frames5, frames1, errors = {}, {}, []
        if observe:
            for symbol in FOCUS_SYMBOLS:
                try:
                    frames5[symbol] = self.bars.fetch(symbol, now.date(), now.date())
                except Exception as exc:
                    errors.append({"symbol": symbol, "error": str(exc)})
        active_dates = {}
        for state in states.values():
            active = state["position"] or state["pending"]
            if active:
                day = _instant(active.get("entry_at", active.get("submitted_at"))).date()
                active_dates[(active["symbol"], day)] = None
        for symbol, day in active_dates:
            try:
                frames1[(symbol, day)] = self.bars.fetch(symbol, day, day, interval_minutes=1)
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc)})
        now = self.clock().astimezone(NY)  # Data collection may have crossed a cutoff.
        counts = {}
        for account_id in (ACCOUNT_ID, COMPARATOR_ID):
            with self.session_factory() as db:
                account = db.query(PaperExperimentAccount).filter_by(id=account_id).with_for_update().one()
                config = PaperConfig(**account.config)
                views = []
                for symbol, frame in frames5.items():
                    try:
                        view = entry_signal(symbol, frame, forecasts.get(symbol), now, config)
                        if view:
                            views.append(view)
                    except ValueError as exc:
                        errors.append({"symbol": symbol, "error": str(exc)})
                active = account.state["position"] or account.state["pending"]
                minute = {}
                if active:
                    day = _instant(active.get("entry_at", active.get("submitted_at"))).date()
                    minute[active["symbol"]] = frames1.get((active["symbol"], day))
                prior_state = deepcopy(account.state)
                state, events = transition(prior_state, now, minute, views, config)
                if state["data_uncertain"]:
                    errors.append({"account": account_id, "error": "position_path_unverifiable"})
                state["market_data_errors"] = deepcopy(errors)
                events.insert(0, {"kind": "OBSERVATION", "event_key": f"observation:{now.isoformat()}",
                    "symbol": None, "occurred_at": now.isoformat(), "payload": {
                        "prior_state": prior_state, "views": views,
                        "result_state": deepcopy(state), "event_keys": [e["event_key"] for e in events],
                        "policy_version": account.policy_version, "implementation_sha256": self.implementation_sha256,
                        "minute_sources": {s: f.attrs.get("provenance") if f is not None else None for s,f in minute.items()}}})
                account.state, account.updated_at = state, now.astimezone(UTC).replace(tzinfo=None)
                for event in events:
                    db.add(PaperExperimentEvent(account_id=account_id, **{
                        **event, "occurred_at": _instant(event["occurred_at"]).astimezone(UTC).replace(tzinfo=None)}))
                    if account_id == ACCOUNT_ID and event["kind"] in ("ENTRY", "EXIT"):
                        p = event["payload"]
                        message = (f"PAPER {event['kind']} — {event['symbol']}\n"
                                   + (f"{p['quantity']:.4f} shares @ {p['entry_price']:.4f}; planned loss ${p['planned_loss']:.2f}" if event["kind"] == "ENTRY"
                                      else f"{REASONS.get(p['reason'], p['reason'])}\n"
                                           f"Price movement ${p['quantity']*(p['exit_raw_price']-p['entry_raw_price']):+.4f}; "
                                           f"costs ${p['total_cost']:.4f}; net ${p['net_pnl']:+.4f}")
                                   + f"\nGeneric $100 simulation, costs {config.friction_bps_per_side:g} bps/side. No broker order.")
                        enqueue_in_session(db, message, dedupe_key=f"{account_id}:{event['event_key']}")
                db.commit()
                counts[account_id] = len(events)
        return {"status": "ok" if not errors else "degraded", "events": counts, "errors": errors}

    def report(self):
        with self.session_factory() as db:
            accounts = db.query(PaperExperimentAccount).filter(PaperExperimentAccount.id.in_([ACCOUNT_ID, COMPARATOR_ID])).all()
            output = {"mode": "generic_paper_only", "policy_version": POLICY_VERSION,
                      "implementation_sha256": self.implementation_sha256,
                      "accounts": {}, "no_trade_benchmark_equity": 100.0}
            for account in accounts:
                state = deepcopy(account.state)
                exits = db.query(PaperExperimentEvent).filter_by(account_id=account.id, kind="EXIT").all()
                # Read-only attribution, derived from original events; the transition is unchanged.
                views = db.query(PaperExperimentEvent).filter_by(account_id=account.id, kind="VIEW").all()
                forecast_ids = {r.payload.get("forecast_id") for r in exits}
                forecasts = {r.id: r for r in db.query(IntradayForecast).filter(IntradayForecast.id.in_(forecast_ids)).all()}
                minute_sources = {}
                for trade in exits:
                    observation = db.query(PaperExperimentEvent).filter(
                        PaperExperimentEvent.account_id == account.id, PaperExperimentEvent.kind == "OBSERVATION",
                        PaperExperimentEvent.id < trade.id).order_by(PaperExperimentEvent.id.desc()).first()
                    if observation and trade.event_key in observation.payload.get("event_keys", []):
                        minute_sources[trade.id] = observation.payload.get("minute_sources", {}).get(trade.symbol)
                # Reprice the same fills for sensitivity, not a rerun of the policy at different costs.
                stress = {}
                for name, side_bps, fee in (("25bps_round_trip", 12.5, 0), ("1usd_per_order", 5, 1)):
                    net = 0.0
                    for row in exits:
                        p = row.payload
                        gross = p["quantity"]*(p["exit_raw_price"]-p["entry_raw_price"])
                        costs = p["quantity"]*(p["exit_raw_price"]+p["entry_raw_price"])*side_bps/10000+2*fee
                        net += gross-costs
                    stress[name] = round(net, 6)
                output["accounts"][account.id] = {"policy_version": account.policy_version,
                    "config": account.config, "state": state,
                    "diagnostics": account_diagnostics(state, exits, views, forecasts, minute_sources),
                    "cost_sensitivity_closed_trades_only": stress,
                    "win_rate": state["wins"]/state["closed_trades"] if state["closed_trades"] else None}
            return output

    def events(self, limit=100):
        with self.session_factory() as db:
            rows = db.query(PaperExperimentEvent).order_by(PaperExperimentEvent.id.desc()).limit(min(max(limit,1),500)).all()
            return [{"id": r.id, "account_id": r.account_id, "kind": r.kind, "symbol": r.symbol,
                     "occurred_at": r.occurred_at.isoformat()+"Z", "payload": r.payload} for r in rows]


intraday_paper_service = IntradayPaperService()


def replay_observation(payload, observed_at, config):
    if payload["implementation_sha256"] != implementation_digest():
        raise ValueError("replay_requires_recorded_implementation_version")
    frames = {s: load_archived_bars(source) if source else None
              for s,source in payload["minute_sources"].items()}
    state, events = transition(payload["prior_state"], observed_at, frames, payload["views"], config)
    mismatches = [key for key, value in state.items() if value != payload["result_state"].get(key)
                  and key != "market_data_errors"]
    if [e["event_key"] for e in events] != payload["event_keys"]:
        mismatches.append("event_keys")
    return mismatches
