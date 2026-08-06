"""
Spread snapshot — the paper system's transaction-cost model.

calibrate_costs.py needs real fills; account has none in 90d. Honest fallback:
measure live bid/ask on the ETF universe + held positions, store per-symbol
round-trip cost in bps to data/costs.json. Scorecard (track_record.py) subtracts
these from paper moves so expectancy is after-cost, per deep_research_20260805
point 3 ("fix the cost model before trusting any backtest").

Round-trip cost = 2 x half-spread + sell-side regulatory fees (~0.9 bps).
Session matters: premarket spreads are 5-10x regular-hours. Snapshot tags the
session; the RTH cron (14:35 UTC) overwrites premarket numbers with honest ones.

Run: python3 scripts/snapshot_spreads.py
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

UNIVERSE = ["SPY", "QQQ", "SMH", "XLE", "XLK", "XLF", "XLV", "XLI", "XLP", "XLY"]
SELL_REG_FEES_BPS = 0.9   # SEC ~0.8 bps + FINRA TAF remainder
FALLBACK_RT_BPS = 5.0     # conservative default when a symbol has no quote
COSTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "costs.json")


def _session_label(now_utc: datetime) -> str:
    """US equity session bucket for spread honesty (UTC, EDT assumed)."""
    hm = now_utc.hour * 60 + now_utc.minute
    if 13 * 60 + 30 <= hm < 20 * 60:
        return "regular"
    if 8 * 60 <= hm < 13 * 60 + 30:
        return "premarket"
    return "afterhours"


def snapshot(symbols=None) -> dict:
    from services.moomoo_service import moomoo_service

    if not moomoo_service.is_connected and not moomoo_service.connect():
        logger.error("[Spreads] moomoo unavailable — cannot snapshot")
        return {}

    try:
        positions = moomoo_service.get_positions() or []
        held = [(p.get("symbol") or "").split(".")[-1].upper() for p in positions]
    except Exception:
        held = []
    targets = sorted(set((symbols or UNIVERSE) + [h for h in held if h]))

    now = datetime.now(timezone.utc)
    session = _session_label(now)
    out = {"computed_at": now.isoformat(), "session": session,
           "sell_reg_fees_bps": SELL_REG_FEES_BPS, "symbols": {}}

    from futu import SubType
    for sym in targets:
        code = f"US.{sym}"
        try:
            moomoo_service.quote_ctx.subscribe([code], [SubType.QUOTE])
            ret, df = moomoo_service.quote_ctx.get_market_snapshot([code])
            if ret != 0 or df is None or df.empty:
                logger.warning(f"[Spreads] no snapshot for {sym}")
                continue
            row = df.iloc[0]
            bid, ask = float(row.get("bid_price") or 0), float(row.get("ask_price") or 0)
            last = float(row.get("last_price") or 0)
            if bid <= 0 or ask <= 0 or ask < bid:
                logger.warning(f"[Spreads] bad book for {sym}: bid={bid} ask={ask}")
                continue
            mid = (bid + ask) / 2
            spread_bps = (ask - bid) / mid * 10000.0
            round_trip = spread_bps + SELL_REG_FEES_BPS   # half-spread each side + fees
            out["symbols"][sym] = {
                "bid": bid, "ask": ask, "last": last,
                "spread_bps": round(spread_bps, 2),
                "round_trip_bps": round(round_trip, 2),
            }
        except Exception as e:
            logger.warning(f"[Spreads] {sym}: {e}")

    vals = [v["round_trip_bps"] for v in out["symbols"].values()]
    out["default_round_trip_bps"] = round(sorted(vals)[len(vals) // 2], 2) if vals else FALLBACK_RT_BPS
    return out


def run_and_save() -> dict:
    out = snapshot()
    if not out.get("symbols"):
        logger.error("[Spreads] nothing captured — costs.json NOT updated")
        return out
    os.makedirs(os.path.dirname(COSTS_PATH), exist_ok=True)
    existing = {}
    if os.path.exists(COSTS_PATH):
        try:
            with open(COSTS_PATH) as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    # Never let premarket/afterhours numbers overwrite regular-hours ones.
    if out["session"] != "regular" and existing.get("session") == "regular":
        logger.info("[Spreads] keeping existing regular-hours costs; saving snapshot to "
                    "history only")
        out["superseded_by_existing_regular"] = True
    else:
        with open(COSTS_PATH, "w") as f:
            json.dump(out, f, indent=1)
        logger.success(f"[Spreads] costs.json updated ({out['session']}, "
                       f"{len(out['symbols'])} symbols, default RT "
                       f"{out['default_round_trip_bps']} bps)")
    hist_path = COSTS_PATH.replace(".json", "_history.jsonl")
    with open(hist_path, "a") as f:
        f.write(json.dumps(out) + "\n")
    return out


if __name__ == "__main__":
    res = run_and_save()
    for sym, v in sorted(res.get("symbols", {}).items()):
        print(f"{sym:6s} bid={v['bid']:<9.2f} ask={v['ask']:<9.2f} "
              f"spread={v['spread_bps']:.2f}bps RT={v['round_trip_bps']:.2f}bps")
    print(f"session={res.get('session')} default_RT={res.get('default_round_trip_bps')}bps")
