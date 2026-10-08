"""
Trial #15 — Supertrend(4h anchor) trend-following on SPY hourly, in-sample MCPT.

Replicates the strategy family from the Jesse / Claude-Fable-5.1 video (RESEARCH_VIDEOS.md,
2026-09-07 entry) and puts it through OUR validation instead of Jesse's trade-shuffle
Monte Carlo, which cannot detect optimisation overfit.

Strategy (mirrors the video):
  - Supertrend(period, mult) on a 4h ANCHOR built from hourly bars. Only COMPLETED
    anchor bars are visible to the hourly bar (the video's [:-1] slice).
  - Long : anchor ST direction up, close > ST line, close < fast EMA (pullback).
  - Short: anchor ST direction down, close < ST line, close > fast EMA, ADX(14) > thr.
    short_mode 0/1 hyperparameter exactly like the video.
  - Exits: ATR(14) stop-loss / take-profit multiples (close-checked) or ST direction flip.
  - Grid: 3x3x2x2x3x2x2 = 432 combos (video: "at least 200 optimisation iterations").

Statistics (both reported, both permuted):
  - best profit factor  (project standard, services/_legacy/mcpt/profit_factor.py)
  - best Sharpe         (the metric the video optimised)
  net of ROUND_TRIP_BPS from services/_legacy/mcpt/costs.py.

Null: services/_legacy/mcpt/bar_permute.get_permutation — session-aware gap shuffle,
re-optimising the FULL grid on every permutation so the search is inside the null.
p = (1 + #{perm_best >= real_best}) / (1 + N_PERMS).  Pass bar: p < 0.01.

Selection-history note: the hypothesis comes from an external video, not from probes on
this data, but the 432-cell grid is itself a search; the MCPT accounts for that search.
Out-of-sample: NOT available — data/hourly holds exactly the 2y development window.

Read-only. No DB writes.
"""
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from numba import njit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from services._legacy.mcpt.bar_permute import get_permutation  # noqa: E402
from services._legacy.mcpt.costs import PER_FLIP_BPS  # noqa: E402

DATA = os.path.join(ROOT, "data", "hourly", "SPY.parquet")
N_PERMS = int(os.environ.get("N_PERMS", "1000"))
BASE_SEED = 20260907
PASS_P = 0.01
MIN_TRADES = 30
BARS_PER_YEAR = 252 * 7
ANCHOR_BARS = 4  # hourly bars per anchor block

ST_PERIODS = [7, 10, 14]
ST_MULTS = [2.0, 3.0, 4.0]
EMA_FASTS = [9, 21]
ADX_THRS = [20.0, 25.0]
SL_MULTS = [1.5, 2.5, 3.5]
TP_MULTS = [2.0, 3.0]
SHORT_MODES = [0, 1]


# ----------------------------------------------------------------------------- indicators
def wilder(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


def atr(h, l, c, n):
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return wilder(tr, n)


def adx(h, l, c, n=14):
    up = np.diff(h, prepend=h[0])
    dn = -np.diff(l, prepend=l[0])
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(h, l, c, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * wilder(plus_dm, n) / a
        mdi = 100 * wilder(minus_dm, n) / a
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.nan_to_num(dx, nan=0.0)
    return wilder(dx, n)


@njit(cache=True)
def supertrend(h, l, c, a, mult):
    """Returns (direction +1/-1, line) per anchor bar. NaN atr -> direction 0."""
    n = len(c)
    direction = np.zeros(n)
    line = np.full(n, np.nan)
    fu = np.full(n, np.nan)
    fl = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(a[i]):
            continue
        hl2 = (h[i] + l[i]) / 2.0
        ub = hl2 + mult * a[i]
        lb = hl2 - mult * a[i]
        if i == 0 or np.isnan(fu[i - 1]):
            fu[i] = ub
            fl[i] = lb
            direction[i] = 1.0 if c[i] > hl2 else -1.0
            line[i] = fl[i] if direction[i] > 0 else fu[i]
            continue
        fu[i] = ub if (ub < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb if (lb > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if direction[i - 1] > 0:
            direction[i] = -1.0 if c[i] < fl[i] else 1.0
        else:
            direction[i] = 1.0 if c[i] > fu[i] else -1.0
        line[i] = fl[i] if direction[i] > 0 else fu[i]
    return direction, line


# ----------------------------------------------------------------------------- anchor map
def anchor_layout(index: pd.DatetimeIndex):
    """Static mapping hourly bar -> (block id, last COMPLETED block id). Depends only on
    the time index, which permutation preserves."""
    dates = pd.Series(index.date)
    pos_in_day = dates.groupby(dates).cumcount().to_numpy()
    block_in_day = pos_in_day // ANCHOR_BARS
    day_id = (dates != dates.shift(1)).cumsum().to_numpy()
    key = day_id * 10 + block_in_day
    block_id = (pd.Series(key) != pd.Series(key).shift(1)).cumsum().to_numpy() - 1
    starts = np.where(np.diff(block_id, prepend=-1) != 0)[0]
    return block_id, starts


def anchor_ohlc(o, h, l, c, starts):
    ends = np.append(starts[1:], len(c))
    ao = o[starts]
    ah = np.maximum.reduceat(h, starts)
    al = np.minimum.reduceat(l, starts)
    ac = c[ends - 1]
    return ao, ah, al, ac


# ----------------------------------------------------------------------------- strategy
@njit(cache=True)
def run_strategy(close, r_next, st_dir_h, st_line_h, ema, adx_h, atr_h,
                 adx_thr, sl_mult, tp_mult, short_mode, flip_cost):
    """Signal at bar t applies to r_next[t] = log(close[t+1]/close[t]).
    Returns (net_returns array, n_trades)."""
    n = len(close)
    net = np.zeros(n)
    pos = 0
    entry = 0.0
    sl = 0.0
    tp = 0.0
    trades = 0
    for t in range(n - 1):
        d = st_dir_h[t]
        if pos != 0:
            exit_now = False
            if pos > 0 and (d < 0 or close[t] <= sl or close[t] >= tp):
                exit_now = True
            if pos < 0 and (d > 0 or close[t] >= sl or close[t] <= tp):
                exit_now = True
            if exit_now:
                pos = 0
                net[t] -= flip_cost
        if pos == 0 and d != 0 and not np.isnan(ema[t]) and not np.isnan(atr_h[t]):
            if d > 0 and close[t] > st_line_h[t] and close[t] < ema[t]:
                pos = 1
                entry = close[t]
                sl = entry - sl_mult * atr_h[t]
                tp = entry + tp_mult * atr_h[t]
                trades += 1
                net[t] -= flip_cost
            elif (short_mode == 1 and d < 0 and close[t] < st_line_h[t]
                  and close[t] > ema[t] and adx_h[t] > adx_thr):
                pos = -1
                entry = close[t]
                sl = entry + sl_mult * atr_h[t]
                tp = entry - tp_mult * atr_h[t]
                trades += 1
                net[t] -= flip_cost
        if pos != 0:
            net[t] += pos * r_next[t]
    return net, trades


def pf_and_sharpe(net):
    gp = net[net > 0].sum()
    gl = -net[net < 0].sum()
    pf = (gp / gl) if gl > 0 else (np.inf if gp > 0 else 0.0)
    sd = net.std()
    sh = (net.mean() / sd * np.sqrt(BARS_PER_YEAR)) if sd > 0 else 0.0
    return float(pf), float(sh)


def optimize(df: pd.DataFrame, layout, want_detail=False):
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    block_id, starts = layout
    ao, ah, al, ac = anchor_ohlc(o, h, l, c, starts)
    r_next = np.append(np.diff(np.log(c)), 0.0)
    atr_h = atr(h, l, c, 14)
    adx_h = adx(h, l, c, 14)
    emas = {e: pd.Series(c).ewm(span=e, adjust=False).mean().to_numpy() for e in EMA_FASTS}
    # hourly bar sees the last COMPLETED anchor block
    prev_block = block_id - 1

    best_pf, best_sh = -1.0, -np.inf
    best_pf_cfg, best_sh_cfg = None, None
    detail = None
    for sp in ST_PERIODS:
        a_anchor = atr(ah, al, ac, sp)
        for sm in ST_MULTS:
            d_a, line_a = supertrend(ah, al, ac, a_anchor, sm)
            st_dir_h = np.where(prev_block >= 0, d_a[np.clip(prev_block, 0, None)], 0.0)
            st_line_h = np.where(prev_block >= 0, line_a[np.clip(prev_block, 0, None)], np.nan)
            for ef in EMA_FASTS:
                for short_mode in SHORT_MODES:
                    for adx_thr in (ADX_THRS if short_mode else [ADX_THRS[0]]):
                        for slm in SL_MULTS:
                            for tpm in TP_MULTS:
                                net, ntr = run_strategy(
                                    c, r_next, st_dir_h, st_line_h, emas[ef], adx_h, atr_h,
                                    adx_thr, slm, tpm, short_mode, PER_FLIP_BPS)
                                if ntr < MIN_TRADES:
                                    continue
                                pf, sh = pf_and_sharpe(net)
                                cfg = dict(st_period=sp, st_mult=sm, ema=ef, short_mode=short_mode,
                                           adx_thr=adx_thr, sl_mult=slm, tp_mult=tpm, trades=ntr)
                                if pf > best_pf:
                                    best_pf, best_pf_cfg = pf, cfg
                                if sh > best_sh:
                                    best_sh, best_sh_cfg = sh, cfg
                                    if want_detail:
                                        detail = net.copy()
    return best_pf, best_pf_cfg, best_sh, best_sh_cfg, detail


# ----------------------------------------------------------------------------- MCPT
_G = {}


def _init(df, layout):
    _G["df"], _G["layout"] = df, layout


def _one_perm(i):
    perm = get_permutation(_G["df"].copy(), seed=BASE_SEED + i)
    bpf, _, bsh, _, _ = optimize(perm, _G["layout"])
    return bpf, bsh


def describe_real(net, index):
    eq = np.cumsum(net)
    dd = eq - np.maximum.accumulate(eq)
    yearly = pd.Series(net, index=index).groupby(index.year).sum()
    print(f"  total log return {eq[-1]:+.4f}   max drawdown {dd.min():+.4f}")
    for y, v in yearly.items():
        print(f"    {y}: {v:+.4f}")
    bh = np.log(_G["df"]["close"].iloc[-1] / _G["df"]["close"].iloc[0])
    print(f"  buy-and-hold same window: {bh:+.4f}")


def main():
    df = pd.read_parquet(DATA)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    layout = anchor_layout(df.index)
    _init(df, layout)
    print("=" * 78)
    print("  TRIAL #15 — SUPERTREND(4h) TREND-FOLLOW on SPY hourly — IN-SAMPLE MCPT")
    print(f"  window {df.index[0]} -> {df.index[-1]}  ({len(df)} bars, "
          f"{layout[1].size} anchor blocks)  grid 432  perms {N_PERMS}  "
          f"cost {2*PER_FLIP_BPS*1e4:.0f} bps round trip")
    print("=" * 78)

    t0 = time.time()
    real_pf, pf_cfg, real_sh, sh_cfg, net = optimize(df, layout, want_detail=True)
    print(f"\n  REAL best PF     {real_pf:.3f}  cfg {pf_cfg}")
    print(f"  REAL best Sharpe {real_sh:.3f}  cfg {sh_cfg}")
    describe_real(net, df.index)
    print(f"  (real optimisation took {time.time()-t0:.1f}s)")

    p0 = get_permutation(df.copy(), seed=BASE_SEED)
    p1 = get_permutation(df.copy(), seed=BASE_SEED + 1)
    pr = get_permutation(df.copy(), seed=BASE_SEED)
    assert not np.allclose(p0["close"], p1["close"]), "seed trap"
    assert np.allclose(p0["close"], pr["close"]), "determinism"
    print("  seed self-check OK")

    ge_pf = ge_sh = 0
    pfs, shs = [], []
    t0 = time.time()
    with ProcessPoolExecutor(initializer=_init, initargs=(df, layout)) as ex:
        for k, (bpf, bsh) in enumerate(ex.map(_one_perm, range(N_PERMS), chunksize=4), 1):
            pfs.append(bpf)
            shs.append(bsh)
            ge_pf += bpf >= real_pf
            ge_sh += bsh >= real_sh
            if k % 50 == 0:
                el = time.time() - t0
                print(f"    perm {k}/{N_PERMS}  >=real: PF {ge_pf}  Sharpe {ge_sh}  "
                      f"[{el:.0f}s, ETA {el/k*(N_PERMS-k):.0f}s]", flush=True)

    p_pf = (1 + ge_pf) / (1 + N_PERMS)
    p_sh = (1 + ge_sh) / (1 + N_PERMS)
    pfs, shs = np.array(pfs), np.array(shs)
    print("\n" + "=" * 78)
    print(f"  best-PF     : real {real_pf:.3f}  perm median {np.median(pfs):.3f}  "
          f"p95 {np.percentile(pfs,95):.3f}  max {pfs.max():.3f}   p = {p_pf:.4f}")
    print(f"  best-Sharpe : real {real_sh:.3f}  perm median {np.median(shs):.3f}  "
          f"p95 {np.percentile(shs,95):.3f}  max {shs.max():.3f}   p = {p_sh:.4f}")
    verdict = "PASS" if (p_pf < PASS_P and p_sh < PASS_P) else "KILL"
    print(f"\n  VERDICT (p < {PASS_P} on BOTH statistics): {verdict}")
    print("  Note: an optimised grid on permuted noise ALSO finds 'good' parameters —")
    print("  that is exactly what the perm median shows and what Jesse's MC cannot.")
    print("=" * 78)


if __name__ == "__main__":
    main()
