"""
Snapback Step 1b — corrected Monte Carlo Permutation Test (plan: 11_snapback_plan.md).

Tests whether the RSI(2)<10 -> long, next-open entry, h-day hold strategy's edge
survives against a null that destroys temporal structure but preserves the return
distribution AND cross-ETF correlation:

  - JOINT permutation: ~/mcpt bar_permute.get_permutation in list mode (shared
    permutation indices across all ETFs). Independent per-ETF permutation would
    shrink the null variance of any pooled statistic and make p spuriously small.
  - Honest statistic: EVENT-level, deduplicated and non-overlapping — same-day
    triggers across ETFs collapse to ONE market event (equal-weight the triggering
    ETFs); no new event is taken while one is held. This matches how a live
    prediction stream behaves and how the 1a gate variant trades.
  - Statistic value: total log return of that event stream. NOT win rate (win rate
    hides loss tails).
  - Seeds: seed = BASE_SEED + i per iteration. bar_permute calls np.random.seed(seed)
    per invocation — passing the SAME seed 1000x would yield 1000 identical
    permutations and a garbage p-value. Self-check asserts iteration 0 != iteration 1
    and same-seed reproducibility.
  - Alignment: joint mode hard-asserts identical indexes -> inner-join across ETFs;
    the resulting common window is REPORTED. If it starts after 2010-01-01 (would
    amputate the 2008 bear stratum), a second run on the longest-history subset is
    performed automatically.

p = (1 + #{perm_stat >= real_stat}) / (1 + N_PERMS).  Pass bar: p < 0.01 at BOTH holds.

Selection-history note (multiplicity): this hypothesis was selected from a 6-cell
grid (RSI2<10 / RSI14<30 x 1/3/5d holds) after weeks of prior probes on the same
~300d window. This in-sample MCPT is therefore a SCREEN; the out-of-window defense
is 1a's 2000-present sample, which never participated in selection.

Note: scripts/mcpt_snapback_quick300.py is a separate quick screen (300d moomoo
window, independent per-ETF return shuffles, close entry, drift-adjusted mean
statistic) written by a parallel session — kept for comparison; it is NOT the gate.

Read-only. No DB writes. yfinance auto_adjust=True PINNED.
"""
import os
import sys
import time
import numpy as np
import pandas as pd

MCPT_DIR = os.path.expanduser("~/mcpt")
if not os.path.isdir(MCPT_DIR):
    sys.exit(f"FATAL: ~/mcpt not found at {MCPT_DIR} — clone kf's mcpt repo first "
             f"(bar_permute.py is required for the permutation null).")
sys.path.insert(0, MCPT_DIR)
try:
    from bar_permute import get_permutation
except ImportError as e:
    sys.exit(f"FATAL: could not import bar_permute from {MCPT_DIR}: {e}")

ETFS = ["SPY", "QQQ", "SMH", "XLE", "XLK", "XLF", "XLV", "XLI", "XLP", "XLY"]
START = "2000-01-01"
HOLDS = [3, 5]
N_PERMS = 1000
BASE_SEED = 20260730
PASS_P = 0.01


def wilder_rsi_np(close_df: pd.DataFrame, length: int = 2) -> pd.DataFrame:
    """Vectorized Wilder RSI over a wide close-price DataFrame (columns = ETFs)."""
    d = close_df.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    rd = dn.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    rsi = 100 - 100 / (1 + ru / rd)
    rsi = rsi.where(rd != 0, 100.0)
    return rsi


def fetch_aligned(etfs):
    import yfinance as yf
    raw = yf.download(etfs, start=START, auto_adjust=True, progress=False, group_by="ticker")
    frames = {}
    for etf in etfs:
        df = raw[etf].dropna(subset=["Open", "Close"]).copy()
        df.columns = [c.lower() for c in df.columns]
        frames[etf] = df[["open", "high", "low", "close"]]
    common = None
    for df in frames.values():
        common = df.index if common is None else common.intersection(df.index)
    aligned = [frames[e].loc[common].copy() for e in etfs]
    return aligned, common


def event_stream_stat(ohlc_list, hold):
    """Total log return of the deduplicated, non-overlapping, next-open event strategy."""
    closes = pd.DataFrame({i: df["close"] for i, df in enumerate(ohlc_list)})
    opens = pd.DataFrame({i: df["open"] for i, df in enumerate(ohlc_list)})
    rsi = wilder_rsi_np(closes, 2)
    trig = (rsi < 10).to_numpy()
    c, o = closes.to_numpy(), opens.to_numpy()
    n = len(c)
    total, n_events, held_until = 0.0, 0, -1
    rets_all = []
    for t in range(5, n - hold - 1):
        if t <= held_until:
            continue
        idx = np.where(trig[t])[0]
        if len(idx) == 0:
            continue
        entry, exit_ = o[t + 1, idx], c[t + hold, idx]
        ok = entry > 0
        if not ok.any():
            continue
        r = np.log(exit_[ok] / entry[ok]).mean()  # equal-weight the triggering ETFs
        total += r
        rets_all.append(r)
        n_events += 1
        held_until = t + hold
    return total, n_events, rets_all


def run_mcpt(aligned, common, label):
    print(f"\n{'='*78}\n  MCPT [{label}] — {len(aligned)} ETFs, window "
          f"{common[0].date()} -> {common[-1].date()} ({len(common)} bars), "
          f"{N_PERMS} perms, base seed {BASE_SEED}")
    if common[0] > pd.Timestamp("2010-01-01"):
        print("  WARNING: common window starts post-2010 — 2008 bear stratum amputated here.")

    # seed-variation self-check (the same-seed trap fabricates p-values)
    p0 = get_permutation([df.copy() for df in aligned], seed=BASE_SEED + 0)
    p1 = get_permutation([df.copy() for df in aligned], seed=BASE_SEED + 1)
    assert not np.allclose(p0[0]["close"].to_numpy(), p1[0]["close"].to_numpy()), \
        "seed trap: iterations 0 and 1 produced identical permutations"
    pr = get_permutation([df.copy() for df in aligned], seed=BASE_SEED + 0)
    assert np.allclose(p0[0]["close"].to_numpy(), pr[0]["close"].to_numpy()), \
        "determinism: same seed did not reproduce the same permutation"
    print("  seed self-check: iter0 != iter1, same-seed reproducible — OK")

    results = {}
    for hold in HOLDS:
        real_stat, real_n, real_rets = event_stream_stat(aligned, hold)
        print(f"\n  hold {hold}d: REAL total log ret {real_stat:+.4f} over {real_n} "
              f"deduplicated non-overlapping events "
              f"(mean/event {100*np.mean(real_rets):+.3f}%)")

        ge, t0 = 0, time.time()
        perm_stats = []
        for i in range(N_PERMS):
            perm = get_permutation([df.copy() for df in aligned], seed=BASE_SEED + i)
            s, _, _ = event_stream_stat(perm, hold)
            perm_stats.append(s)
            ge += s >= real_stat
            if (i + 1) % 100 == 0:
                el = time.time() - t0
                print(f"    perm {i+1}/{N_PERMS}  (>=real so far: {ge})  "
                      f"[{el:.0f}s elapsed, ETA {el/(i+1)*(N_PERMS-i-1):.0f}s]", flush=True)
        p = (1 + ge) / (1 + N_PERMS)
        ps = np.array(perm_stats)
        print(f"  hold {hold}d: p = {p:.4f}  (perm dist mean {ps.mean():+.4f}, "
              f"p95 {np.percentile(ps,95):+.4f}, max {ps.max():+.4f}; real {real_stat:+.4f})")
        results[hold] = p
    return results


def run():
    print("=" * 78)
    print("  SNAPBACK 1b — CORRECTED MCPT (joint permutation, event-dedup, non-overlap)")
    print("=" * 78)
    aligned, common = fetch_aligned(ETFS)
    results = run_mcpt(aligned, common, "all-10")

    extra = {}
    if common[0] > pd.Timestamp("2010-01-01"):
        subset = ["SPY", "QQQ", "XLE", "XLK", "XLF", "XLV", "XLI", "XLP", "XLY"]
        aligned_s, common_s = fetch_aligned(subset)
        extra = run_mcpt(aligned_s, common_s, "longest-history subset")

    print(f"\n{'='*78}\n  1b VERDICT (pass bar: p < {PASS_P} at BOTH holds, all-10 run)")
    overall = True
    for hold, p in results.items():
        ok = p < PASS_P
        overall = overall and ok
        print(f"  hold {hold}d: p = {p:.4f}  {'PASS' if ok else 'FAIL'}")
    for hold, p in extra.items():
        print(f"  [subset] hold {hold}d: p = {p:.4f}  (informational)")
    print(f"\n  1b OVERALL: {'PASS' if overall else 'KILL'}")
    print("  Selection-history note: hypothesis chosen from a 6-cell grid + prior probes")
    print("  on the same 300d window — treat 1b as a screen; 1a is the out-of-window test.")
    print("=" * 78)


if __name__ == "__main__":
    run()
