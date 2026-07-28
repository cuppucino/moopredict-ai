"""
Calibrate the focus engine's per-ETF prior from history (2026-07-28).

The engine starts every ETF at a flat 0.54 up-drift. But SPY/QQQ/SMH/XLE drift up at different
rates and have different day-to-day noise. This measures each one's ACTUAL up-day rate over ~1yr
(pure direction, the honest base to start from) and checks whether a simple technical rule
(RSI momentum) beats that base — so we know how much, if any, weight the technical half deserves.

Can't calibrate the news weight here (no historical news) — that's measured live.

Run: python3 scripts/calibrate_focus.py   (read-only)
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd  # noqa: E402
from services.ta_engine import ta_engine  # noqa: E402

ETFS = ["SPY", "QQQ", "SMH", "XLE"]
NUM = 300


def run():
    print("=" * 58)
    print("  FOCUS CALIBRATION — per-ETF up-drift base rate + does TA help?")
    print("=" * 58)
    priors = {}
    for etf in ETFS:
        try:
            df = ta_engine._get_kline_data(etf, num=NUM)
        except Exception:
            df = None
        if df is None or len(df) < 60:
            print(f"  {etf}: no data"); continue
        c = df["close"].astype(float).reset_index(drop=True)
        nxt = c.shift(-1) > c                     # next day up?
        base = float(nxt[:-1].mean())             # pure up-rate = the prior
        priors[etf] = round(base, 3)

        rsi = df.ta.rsi(length=14)                # simple technical rule
        macd = df.ta.macd()
        tacc = tn = 0
        for i in range(30, len(c) - 1):
            if pd.isna(rsi.iloc[i]) or macd is None or macd.empty:
                continue
            hist = macd.iloc[i, 1] if macd.shape[1] > 1 else 0
            pred_up = (rsi.iloc[i] > 50) and (hist > 0)   # momentum: RSI>50 AND MACD hist>0
            tacc += (pred_up == bool(nxt.iloc[i])); tn += 1
        ta_wr = 100 * tacc / tn if tn else 0
        print(f"  {etf}: base up-rate = {100*base:4.1f}%   |   RSI+MACD rule = {ta_wr:4.1f}% (n={tn})"
              f"   {'✅ TA helps' if ta_wr > 100*base + 1 else 'TA no better than base'}")

    print("\n  Calibrated priors to use in focus_engine (per-ETF, not flat 0.54):")
    print("  PRIORS = " + str(priors))
    print("=" * 58)


if __name__ == "__main__":
    run()
