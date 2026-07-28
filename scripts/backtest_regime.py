"""
HMM-style regime backtest (2026-07-29) — does a PROPER regime brain beat our crude one?

From kf's source: Hidden Markov Models infer the market's hidden state from returns+volatility,
so you can switch strategy by state. hmmlearn isn't installed; sklearn GaussianMixture on
(1d ret, 5d ret, 10d realized vol) is the honest stand-in (states without transition dynamics,
plus a persistence smoother). Fit on SPY, then ask the only question that matters for us:

  In each detected state, what is the NEXT-DAY up-rate of our 4 focus ETFs?
  A DOWN call only makes sense in a state whose up-rate is meaningfully < 50%.

Policies compared on pure next-day direction accuracy:
  P0 always-UP (the 53.9% base to beat)
  P1 GMM-switched: UP everywhere except DOWN in the bear/high-vol state
  P2 crude current regime (SPY 5d ±1.5% band): DOWN only in TREND_DOWN
Honesty: GMM fit on the full sample = mild lookahead; if a state shows real signal we re-test
split-sample before believing it. Read-only.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.mixture import GaussianMixture  # noqa: E402
from services.ta_engine import ta_engine  # noqa: E402

ETFS = ["SPY", "QQQ", "SMH", "XLE"]
NUM = 300


def closes(sym):
    df = ta_engine._get_kline_data(sym, num=NUM)
    if df is None or len(df) < 80:
        return None
    return df["close"].astype(float).reset_index(drop=True)


def run():
    spy = closes("SPY")
    if spy is None:
        print("no SPY"); return
    r1 = spy.pct_change()
    r5 = spy.pct_change(5)
    vol10 = r1.rolling(10).std()
    feat = pd.concat([r1, r5, vol10], axis=1).dropna()
    X = feat.values
    idx0 = feat.index[0]

    gmm = GaussianMixture(n_components=3, covariance_type="full", random_state=7, n_init=5).fit(X)
    raw_states = gmm.predict(X)
    # persistence smoothing (HMM-ish): a state must hold 2 days to switch
    states = raw_states.copy()
    for i in range(1, len(states)):
        if states[i] != states[i - 1] and (i + 1 >= len(states) or raw_states[i + 1] != raw_states[i]):
            states[i] = states[i - 1]

    # label states by their mean daily return / vol
    stats = {}
    for s in range(3):
        m = raw_states == s
        stats[s] = (float(np.mean(X[m, 0])), float(np.mean(X[m, 2])), int(m.sum()))
    bear = min(stats, key=lambda s: stats[s][0])          # most negative mean return
    calm = max(stats, key=lambda s: stats[s][0])
    print("=" * 60)
    print("  REGIME BACKTEST — GMM(3) on SPY (ret1, ret5, vol10)")
    for s in range(3):
        tag = "BEAR/high-vol" if s == bear else ("BULL/calm" if s == calm else "MID/chop")
        print(f"  state {s} [{tag:13}] days={stats[s][2]:3}  mean_ret={stats[s][0]*100:+.2f}%  vol={stats[s][1]*100:.2f}%")

    # crude regime series aligned to same index
    crude = []
    for i in range(len(spy)):
        if i < 5:
            crude.append("UNK"); continue
        ch = (spy[i] - spy[i - 5]) / spy[i - 5] * 100
        crude.append("CHOP" if abs(ch) < 1.5 else ("TU" if ch > 0 else "TD"))

    print("\n  Next-day ETF up-rate BY STATE (the decisive diagnostic):")
    agg = {"P0": [0, 0], "P1": [0, 0], "P2": [0, 0]}
    for etf in ETFS:
        c = closes(etf)
        if c is None:
            continue
        m = min(len(c), len(spy))
        c = c[-m:].reset_index(drop=True)
        per_state = {s: [0, 0] for s in range(3)}
        for j, gi in enumerate(feat.index):
            if gi >= m - 1 or gi < 0:
                continue
            up = c[gi + 1] > c[gi]
            s = states[j]
            per_state[s][0] += up; per_state[s][1] += 1
            # policies
            agg["P0"][0] += up; agg["P0"][1] += 1
            p1_up = (s != bear)
            agg["P1"][0] += (p1_up == up); agg["P1"][1] += 1
            p2_up = (crude[gi] != "TD")
            agg["P2"][0] += (p2_up == up); agg["P2"][1] += 1
        row = "  " + etf + ": " + "  ".join(
            f"s{s}({'B' if s==bear else 'C' if s==calm else 'M'})={100*per_state[s][0]/per_state[s][1]:4.1f}%(n={per_state[s][1]})"
            for s in range(3) if per_state[s][1])
        print(row)

    print("\n  POLICY ACCURACY (all 4 ETFs pooled, pure next-day direction):")
    for k, name in [("P0", "always-UP (base)"), ("P1", "GMM-switch: DOWN in bear state"),
                    ("P2", "crude regime: DOWN in TREND_DOWN")]:
        a, t = agg[k]
        print(f"  {name:34} n={t:<5} acc={100*a/t:5.1f}%")
    print("=" * 60)
    print("  Note: GMM fit is full-sample (mild lookahead). If P1 beats P0 clearly,")
    print("  re-test split-sample before believing it.")


if __name__ == "__main__":
    run()
