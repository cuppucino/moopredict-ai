"""
Extreme-conditioned mean reversion backtest (2026-07-29) — testing kf's source's Q6 claim.

Source: mean reversion is the ONLY broadly profitable strategy family (9,000-strategy study),
via RSI snapbacks at EXTREMES on daily bars. Our earlier test said next-day mean reversion is a
coin flip — but we tested predicting EVERY day at a 1-day hold. Their edge (if real) lives in
SELECTIVE entries (only at oversold extremes) and MULTI-DAY holds. That's what this tests:

  Entry: RSI(14) < 30 (oversold extreme) at close  ->  long
  Holds: 1, 3, 5 days.  Win = positive return over the hold.
  Benchmarks: the SAME ETF's unconditional up-rate at the same hold length.

Also the RSI(2) < 10 variant (a classic short-term mean-reversion trigger).
Read-only. Sample sizes will be small (extremes are rare) — read as indicative.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd  # noqa: E402
from services.ta_engine import ta_engine  # noqa: E402

ETFS = ["SPY", "QQQ", "SMH", "XLE", "XLK", "XLF", "XLV", "XLI", "XLP", "XLY"]
NUM = 300
HOLDS = [1, 3, 5]


def run():
    agg = {}   # (variant, hold) -> [wins, n, sum_ret]
    base = {}  # hold -> [ups, n]
    for etf in ETFS:
        try:
            df = ta_engine._get_kline_data(etf, num=NUM)
        except Exception:
            df = None
        if df is None or len(df) < 60:
            continue
        c = df["close"].astype(float).reset_index(drop=True)
        rsi14 = df.ta.rsi(length=14).reset_index(drop=True)
        rsi2 = df.ta.rsi(length=2).reset_index(drop=True)
        for i in range(20, len(c) - max(HOLDS)):
            for h in HOLDS:
                ret = (c[i + h] - c[i]) / c[i]
                b = base.setdefault(h, [0, 0]); b[0] += ret > 0; b[1] += 1
                if not pd.isna(rsi14[i]) and rsi14[i] < 30:
                    a = agg.setdefault(("RSI14<30", h), [0, 0, 0.0])
                    a[0] += ret > 0; a[1] += 1; a[2] += ret
                if not pd.isna(rsi2[i]) and rsi2[i] < 10:
                    a = agg.setdefault(("RSI2<10", h), [0, 0, 0.0])
                    a[0] += ret > 0; a[1] += 1; a[2] += ret

    print("=" * 62)
    print("  EXTREME MEAN-REVERSION — selective entries, multi-day holds")
    print(f"  {len(ETFS)} ETFs, ~{NUM}d.  Win = positive return over hold.")
    print("=" * 62)
    print(f"  {'variant':12} {'hold':>4} {'n':>5} {'win%':>7} {'avg ret':>9}   vs base")
    for (v, h), (w, n, s) in sorted(agg.items()):
        bw, bn = base[h]
        brate = 100 * bw / bn
        rate = 100 * w / n if n else 0
        edge = rate - brate
        print(f"  {v:12} {h:>3}d {n:>5} {rate:6.1f}% {100*s/n:+8.2f}%   base {brate:4.1f}%  edge {edge:+.1f}pp")
    print("=" * 62)
    print("  Positive edge at 3-5d holds = the source's claim confirmed on OUR data.")


if __name__ == "__main__":
    run()
