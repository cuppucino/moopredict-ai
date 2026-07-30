"""
MCPT validation of the RSI(2)<10 snapback edge (2026-07-30).

Methodology per kf's source (and the ~/mcpt toolkit): permutation-test the edge. We shuffle
each ETF's daily returns 1000 times — every permuted path keeps the exact same return
distribution (mean/std/skew) but destroys the SEQUENCE. RSI extremes still occur on shuffled
paths; if "buy the extreme, hold 3d" earns as much on shuffled noise as on the real sequence,
the edge is fake (it would just be the distribution, not the dynamics).

Statistic: pooled mean 3-day forward return AFTER RSI(2)<10 entries MINUS the unconditional
mean 3-day return (the drift), across 10 ETFs. p-value = fraction of permutations whose edge
>= the real edge. Source's bar: p < 0.01 (1%). Read-only.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from services.ta_engine import ta_engine  # noqa: E402

ETFS = ["SPY", "QQQ", "SMH", "XLE", "XLK", "XLF", "XLV", "XLI", "XLP", "XLY"]
NUM, HOLD, RSI_P, RSI_LVL, N_PERM = 300, 3, 2, 10.0, 1000


def rsi(closes: np.ndarray, period: int) -> np.ndarray:
    """Wilder RSI, vectorized via ewm."""
    s = pd.Series(closes)
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).to_numpy()


def edge_on_path(closes: np.ndarray):
    """(edge, n_trades): mean 3d fwd return after RSI2<10 minus unconditional mean 3d return."""
    r = rsi(closes, RSI_P)
    fwd = np.full(len(closes), np.nan)
    fwd[:-HOLD] = closes[HOLD:] / closes[:-HOLD] - 1.0
    valid = ~np.isnan(fwd)
    valid[:10] = False
    sig = valid & (r < RSI_LVL)
    if sig.sum() == 0:
        return 0.0, 0
    return float(np.nanmean(fwd[sig]) - np.nanmean(fwd[valid])), int(sig.sum())


def run():
    rng = np.random.default_rng(7)
    series = {}
    for etf in ETFS:
        df = ta_engine._get_kline_data(etf, num=NUM)
        if df is not None and len(df) > 80:
            series[etf] = df["close"].astype(float).to_numpy()
    if not series:
        print("no data"); return

    # real pooled edge (trade-weighted)
    tot_e = tot_n = 0.0
    for c in series.values():
        e, n = edge_on_path(c)
        tot_e += e * n; tot_n += n
    real_edge = tot_e / tot_n if tot_n else 0.0
    print(f"real pooled edge: {real_edge*100:+.3f}% per trade over drift (n={int(tot_n)} trades)")

    # permutations: shuffle each ETF's daily returns, rebuild path, same statistic
    rets = {k: np.diff(np.log(c)) for k, c in series.items()}
    beats = 0
    perm_edges = []
    for i in range(N_PERM):
        te = tn = 0.0
        for k, r0 in rets.items():
            pr = rng.permutation(r0)
            path = series[k][0] * np.exp(np.cumsum(np.insert(pr, 0, 0.0)))
            e, n = edge_on_path(path)
            te += e * n; tn += n
        pe = te / tn if tn else 0.0
        perm_edges.append(pe)
        if pe >= real_edge:
            beats += 1
        if (i + 1) % 200 == 0:
            print(f"  ... {i+1}/{N_PERM} perms, running p={beats/(i+1):.4f}")

    p = beats / N_PERM
    pm = float(np.mean(perm_edges))
    print("=" * 56)
    print(f"  MCPT RESULT — RSI({RSI_P})<{RSI_LVL:g}, {HOLD}d hold, {len(series)} ETFs, {N_PERM} perms")
    print(f"  real edge:      {real_edge*100:+.3f}%/trade")
    print(f"  perm mean edge: {pm*100:+.3f}%/trade")
    print(f"  p-value: {p:.4f}  ({beats}/{N_PERM} perms matched or beat the real edge)")
    print(f"  verdict: {'✅ REAL (p<0.01)' if p < 0.01 else '⚠️ NOT PROVEN at p<0.01' if p < 0.05 else '❌ LIKELY FAKE (p>=0.05)'}")
    print("=" * 56)


if __name__ == "__main__":
    run()
