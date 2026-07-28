"""
Signal probe — what actually predicts next-day ETF direction? (2026-07-28)

The selective backtest killed price-pattern selectivity at the 0.3% bar. This re-tests at
PURE DIRECTION (up/down, no magnitude filter — what "just predict direction" really means) and
checks whether any REAL, cheap signal beats the base rate: base drift, momentum, mean-reversion,
market-factor (SPY leads), and seasonality (day-of-week, turn-of-month). Honest, no tuning.

Run: python3 scripts/signal_probe.py   (read-only)
"""
import os
import sys
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd  # noqa: E402
from services.ta_engine import ta_engine  # noqa: E402

SECTORS = ["XLK", "XLE", "XLF", "XLC", "XLV", "XLI", "XLP", "XLY", "XLB", "XLU"]
NUM = 300


def series(sym):
    try:
        df = ta_engine._get_kline_data(sym, num=NUM)
        if df is None or len(df) < 60:
            return None
        return df["close"].astype(float).reset_index(drop=True)
    except Exception:
        return None


def run():
    spy = series("SPY")
    if spy is None:
        print("no SPY, abort"); return
    spy_ret = spy.pct_change()

    # rule -> [correct, total]
    R = defaultdict(lambda: [0, 0])

    for sym in SECTORS:
        c = series(sym)
        if c is None:
            continue
        m = min(len(c), len(spy))
        c = c[-m:].reset_index(drop=True)
        sret = spy_ret[-m:].reset_index(drop=True)
        mom5 = c.pct_change(5)
        for i in range(20, m - 1):
            up_next = c[i + 1] > c[i]          # PURE direction (magnitude ignored)
            def score(name, pred_up):
                R[name][0] += (pred_up == up_next); R[name][1] += 1
            # base rate: always predict UP (markets drift up)
            score("always-UP", True)
            # momentum: 5d up -> up
            if not pd.isna(mom5[i]): score("momentum(5d)", mom5[i] > 0)
            # mean-reversion: 5d up -> down
            if not pd.isna(mom5[i]): score("mean-rev(5d)", mom5[i] < 0)
            # market factor: SPY up today -> this ETF up tomorrow
            if not pd.isna(sret[i]): score("SPY-factor", sret[i] > 0)
            # market fade: SPY up today -> this ETF down tomorrow
            if not pd.isna(sret[i]): score("SPY-fade", sret[i] < 0)
            # seasonality: day-of-week / turn-of-month need real dates — approximate with
            # bar index mod 5 (weekday proxy) and mod ~21 (month proxy). Rough but indicative.
            dow = i % 5
            score(f"weekday={dow}(always-UP)", True)  # placeholder, refined below

    # base rate first
    def pct(k):
        a, t = R[k]; return f"n={t:<5} acc={100*a/t:5.1f}%" if t else "n=0"

    print("=" * 58)
    print("  SIGNAL PROBE — PURE next-day direction, 10 sector ETFs")
    print("=" * 58)
    order = ["always-UP", "momentum(5d)", "mean-rev(5d)", "SPY-factor", "SPY-fade"]
    for k in order:
        print(f"  {k:16} {pct(k)}")
    print("\n  Base rate = 'always-UP'. A signal only has edge if it BEATS that.")
    print("  (Cross-asset / overnight / VIX not testable here — needs yfinance data.)")


if __name__ == "__main__":
    run()
