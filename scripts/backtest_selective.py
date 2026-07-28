"""
Backtest the selective thesis on ~1yr of daily history — the aggressive-data play (2026-07-28).

Live accumulation is too slow (0-2 selective/day). This replays the PRICE-BASED core of the
selective gate across hundreds of past days and resolves each against the actual next-day move,
so we get a real win-rate read in minutes instead of weeks.

What it tests (and what it CAN'T):
  CAN replay: regime (SPY 5-day), sector-ETF class, a price direction rule (mean-reversion vs trend).
  CANNOT replay: the news/brief-score signal (no historical per-day news) — so this is the core,
  not the full live gate. Treat as a strong hint, not proof; the live SELECTIVE track is the real
  out-of-sample test.

Rules compared straight (no tuning to the result — that would just overfit):
  - mean-reversion (Phase-7 thesis: in CHOP, fade the deviation from the 20d SMA)
  - trend-follow (the opposite, as a control)
  each measured CHOP-only vs all-regimes, so we can see whether the CHOP filter actually helps.

Run: python3 scripts/backtest_selective.py
Read-only (fetches klines, posts nothing).
"""
import os
import sys
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd  # noqa: E402
from services.ta_engine import ta_engine  # noqa: E402
from services.prediction_service import classify_leverage  # noqa: E402

SECTOR_ETFS = ["XLK", "XLE", "XLF", "XLC", "XLV", "XLI", "XLP", "XLY", "XLB", "XLU"]
CONTROLS = ["SPY", "QQQ", "SOXX"]   # broad + thematic, to check the sector filter matters
THRESHOLD = 0.3        # same 0.3% resolution bar as live
SMA = 20               # deviation reference
REGIME_LOOKBACK = 5    # SPY 5-day, matches the live regime idea
CHOP_BAND = 1.5        # |SPY 5d %| < this = CHOP
NUM_BARS = 300         # ~1yr daily


def closes(symbol):
    try:
        df = ta_engine._get_kline_data(symbol, num=NUM_BARS)
        if df is None or len(df) < SMA + REGIME_LOOKBACK + 5:
            return None
        return df["close"].astype(float).reset_index(drop=True)
    except Exception:
        return None


def spy_regime_series(spy):
    """Per-day regime label from SPY's trailing REGIME_LOOKBACK return."""
    reg = []
    for i in range(len(spy)):
        if i < REGIME_LOOKBACK:
            reg.append("UNKNOWN"); continue
        r = (spy[i] - spy[i - REGIME_LOOKBACK]) / spy[i - REGIME_LOOKBACK] * 100
        reg.append("CHOP" if abs(r) < CHOP_BAND else ("TREND_UP" if r > 0 else "TREND_DOWN"))
    return reg


def run():
    spy = closes("SPY")
    if spy is None:
        print("could not load SPY — aborting"); return
    regime = spy_regime_series(spy)
    n_spy = len(spy)

    # tally[(rule, filt)] -> [right, total]
    tally = defaultdict(lambda: [0, 0])
    per_symbol = defaultdict(lambda: [0, 0])   # sector mean-reversion CHOP-only, per symbol
    regime_days = defaultdict(int)
    for r in regime:
        regime_days[r] += 1

    universe = [(s, "sector") for s in SECTOR_ETFS] + [(s, "control") for s in CONTROLS]
    for sym, kind in universe:
        c = closes(sym)
        if c is None:
            continue
        m = min(len(c), n_spy)
        c = c[-m:].reset_index(drop=True)
        reg = regime[-m:]
        sma = c.rolling(SMA).mean()
        for i in range(SMA, m - 1):          # need SMA history and a next day to resolve
            if pd.isna(sma[i]):
                continue
            dev = (c[i] - sma[i]) / sma[i]   # + = above SMA (extended up), - = below (extended down)
            nxt = (c[i + 1] - c[i]) / c[i] * 100   # next-day % move
            if abs(nxt) < 1e-9:
                continue
            mr_dir = "DOWN" if dev > 0 else "UP"     # mean-reversion: fade the deviation
            tf_dir = "UP" if dev > 0 else "DOWN"     # trend-follow: ride it
            def right(direction):
                return (direction == "UP" and nxt > THRESHOLD) or (direction == "DOWN" and nxt < -THRESHOLD)
            is_chop = reg[i] == "CHOP"
            is_sector = kind == "sector"
            # record the 4 rule/filter combos, sector-only (the live gate is sector-only)
            if is_sector:
                for rule, d in [("mean-rev", mr_dir), ("trend", tf_dir)]:
                    tally[(rule, "all-regime")][0] += right(d); tally[(rule, "all-regime")][1] += 1
                    if is_chop:
                        tally[(rule, "CHOP-only")][0] += right(d); tally[(rule, "CHOP-only")][1] += 1
                if is_chop:
                    per_symbol[sym][0] += right(mr_dir); per_symbol[sym][1] += 1
            else:
                # control: sector filter OFF, mean-rev CHOP-only — to compare vs sector CHOP-only
                if is_chop:
                    tally[("mean-rev", "CONTROL non-sector CHOP")][0] += right(mr_dir)
                    tally[("mean-rev", "CONTROL non-sector CHOP")][1] += 1

    def line(k):
        r, t = tally[k]
        return f"  {k[0]:9} {k[1]:26} n={t:<5} WR={100*r/t:5.1f}%" if t else f"  {k[0]:9} {k[1]:26} n=0"

    print("=" * 60)
    print(f"  SELECTIVE BACKTEST — ~{NUM_BARS}d daily, {len(SECTOR_ETFS)} sector ETFs")
    print(f"  regime days: {dict(regime_days)}")
    print("=" * 60)
    print("\nSector ETFs — the live gate's universe:")
    for k in [("mean-rev", "CHOP-only"), ("mean-rev", "all-regime"),
              ("trend", "CHOP-only"), ("trend", "all-regime")]:
        print(line(k))
    print("\nDoes the sector filter matter? (mean-rev, CHOP-only):")
    print(line(("mean-rev", "CHOP-only")).replace("CHOP-only", "SECTOR CHOP  "))
    print(line(("mean-rev", "CONTROL non-sector CHOP")))
    print("\nPer sector ETF (mean-rev, CHOP-only):")
    for s in sorted(per_symbol, key=lambda s: -(per_symbol[s][0] / per_symbol[s][1] if per_symbol[s][1] else 0)):
        r, t = per_symbol[s]
        if t:
            print(f"  {s:5} n={t:<4} WR={100*r/t:5.1f}%")
    print("\n" + "=" * 60)
    print("  Backtest replays PRICE signals only (no historical news). A strong hint,")
    print("  not proof — the live SELECTIVE track is the real out-of-sample test.")


if __name__ == "__main__":
    run()
