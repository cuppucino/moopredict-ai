"""
Snapback Step 1a — long-history regime-stratified backtest (plan: 11_snapback_plan.md).

Validates the RSI(2)<10 extreme mean-reversion edge on 2000-present yfinance data,
gated on the LIVE-REALIZABLE trade (entry at next-day OPEN — the live system detects
the trigger next morning pre-market, so the trigger-close entry of the original
backtest includes an overnight bounce the live signal cannot capture).

Reports per stratum (full / bear years / bull years / per year):
  n, WR(ret>0), WR(@+0.3% live RIGHT threshold), avg ret, profit factor,
  worst trade, p95 loss, max consecutive losses.
Variants: RSI2<10 -> UP and RSI2>90 -> DOWN (symmetric probe), holds 3d/5d,
entry close-of-trigger (comparison only) vs next-open (THE GATE), overlapping
(all triggers) vs non-overlapping (one position per ETF at a time — matches the
live stream and the 1b MCPT statistic).

Kill conditions (1a): next-open non-overlap pooled edge <= 0, or pooled PF < 1,
or bear-years PF < 0.8.

Also reports the yfinance-vs-moomoo trigger-set disagreement rate over the recent
overlap window (RSI(2) is maximally sensitive to adjustment differences at
ex-dividend bars). Skips gracefully with a loud note if moomoo is unreachable.

Read-only. No DB writes. yfinance auto_adjust=True PINNED explicitly.
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ETFS = ["SPY", "QQQ", "SMH", "XLE", "XLK", "XLF", "XLV", "XLI", "XLP", "XLY"]
START = "2000-01-01"
HOLDS = [3, 5]
BEAR_YEARS = {2008, 2011, 2015, 2018, 2020, 2022}
LIVE_THRESHOLD = 0.003  # +0.3% = live "RIGHT" bar for UP (see prediction_service)


def wilder_rsi(close: pd.Series, length: int) -> pd.Series:
    """Wilder RSI via RMA (ewm alpha=1/length, adjust=False) — pandas_ta-compatible."""
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    rd = dn.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    rs = ru / rd
    rsi = 100 - 100 / (1 + rs)
    rsi[rd == 0] = 100.0
    rsi[(ru == 0) & (rd == 0)] = 50.0
    return rsi


def fetch(etfs, start=START):
    import yfinance as yf
    raw = yf.download(etfs, start=start, auto_adjust=True, progress=False, group_by="ticker")
    out = {}
    for etf in etfs:
        df = raw[etf].dropna(subset=["Open", "Close"]).copy() if etf in raw.columns.get_level_values(0) else None
        if df is None or len(df) < 300:
            print(f"  NOTE: {etf} — insufficient/missing history, skipped")
            continue
        df.columns = [c.lower() for c in df.columns]
        out[etf] = df[["open", "high", "low", "close"]]
    return out


def rsi_parity_check(data):
    """Verify our Wilder RSI matches pandas_ta on real data (live parity anchor)."""
    try:
        import pandas_ta as ta
    except ImportError:
        print("  NOTE: pandas_ta unavailable — RSI parity check skipped")
        return
    worst = 0.0
    for etf, df in data.items():
        ours = wilder_rsi(df["close"], 2)
        ref = ta.rsi(df["close"], length=2)
        diff = (ours - ref).abs().dropna().max()
        worst = max(worst, float(diff))
    print(f"  RSI(2) parity vs pandas_ta: max abs diff {worst:.2e} "
          f"({'OK' if worst < 1e-6 else 'MISMATCH — INVESTIGATE'})")


def trades_for(df, hold, variant, entry_mode, overlap):
    """Return list of (trigger_date, signed_return). variant: 'UP' (RSI2<10) or 'DOWN' (RSI2>90).
    entry_mode: 'next_open' (gate) or 'close' (comparison). Signed = positive when the call is right."""
    c = df["close"].to_numpy()
    o = df["open"].to_numpy()
    rsi2 = wilder_rsi(df["close"], 2).to_numpy()
    dates = df.index
    n = len(c)
    trades, held_until = [], -1
    for i in range(5, n - hold - 1):
        if not overlap and i <= held_until:
            continue
        trig = rsi2[i] < 10 if variant == "UP" else rsi2[i] > 90
        if not trig or np.isnan(rsi2[i]):
            continue
        entry = o[i + 1] if entry_mode == "next_open" else c[i]
        exit_ = c[i + hold]
        if entry <= 0:
            continue
        ret = exit_ / entry - 1
        signed = ret if variant == "UP" else -ret
        trades.append((dates[i], signed))
        if not overlap:
            held_until = i + hold
    return trades


def metrics(trades):
    if not trades:
        return None
    r = np.array([t[1] for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    # max consecutive losses
    mcl = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0
        mcl = max(mcl, cur)
    return {
        "n": len(r),
        "wr": 100 * (r > 0).mean(),
        "wr03": 100 * (r > LIVE_THRESHOLD).mean(),
        "avg": 100 * r.mean(),
        "pf": pf,
        "worst": 100 * r.min(),
        "p95loss": 100 * (np.percentile(-r[r <= 0], 95) if len(losses) else 0.0) * -1,
        "mcl": mcl,
    }


def fmt(m, label):
    if m is None:
        return f"  {label:<22} n=0"
    return (f"  {label:<22} n={m['n']:>4}  WR {m['wr']:5.1f}%  WR@0.3% {m['wr03']:5.1f}%  "
            f"avg {m['avg']:+5.2f}%  PF {m['pf']:5.2f}  worst {m['worst']:+6.2f}%  "
            f"p95loss {m['p95loss']:+6.2f}%  maxConsecL {m['mcl']}")


def base_rates(data, hold):
    ups_cc = ups_oc = n = 0
    for df in data.values():
        c, o = df["close"].to_numpy(), df["open"].to_numpy()
        for i in range(5, len(c) - hold - 1):
            ups_cc += c[i + hold] > c[i]
            ups_oc += c[i + hold] > o[i + 1]
            n += 1
    return 100 * ups_cc / n, 100 * ups_oc / n


def moomoo_parity(data):
    """Trigger-set disagreement rate: yfinance-adjusted vs moomoo QFQ over the overlap window."""
    try:
        from services.ta_engine import ta_engine
        rows = []
        for etf in ETFS:
            mdf = ta_engine._get_kline_data(etf, num=300)
            if mdf is None or len(mdf) < 60 or etf not in data:
                continue
            mdf = mdf.copy()
            mdf["date"] = pd.to_datetime(mdf["time_key"]).dt.normalize()
            m_rsi = wilder_rsi(mdf["close"].astype(float), 2)
            m_trig = pd.Series((m_rsi < 10).to_numpy(), index=mdf["date"].to_numpy())
            y_rsi = wilder_rsi(data[etf]["close"], 2)
            y_trig = (y_rsi < 10)
            y_trig.index = y_trig.index.normalize()
            common = m_trig.index.intersection(y_trig.index)
            if len(common) < 30:
                continue
            a, b = m_trig.loc[common], y_trig.loc[common]
            dis = (a != b).mean()
            either = (a | b).sum()
            rows.append((etf, len(common), 100 * dis, int(either)))
        if not rows:
            print("  moomoo parity: NO OVERLAP DATA — check skipped (run before wiring Step 2)")
            return
        for etf, nn, dis, either in rows:
            print(f"  {etf}: {nn} overlap days, trigger disagreement {dis:.2f}% ({either} trigger days either source)")
    except Exception as e:
        print(f"  moomoo parity: UNAVAILABLE ({type(e).__name__}: {e}) — run before wiring Step 2")


def run():
    print("=" * 78)
    print("  SNAPBACK 1a — LONG-HISTORY REGIME-STRATIFIED BACKTEST (yfinance, adj=True)")
    print("=" * 78)
    data = fetch(ETFS)
    spans = {e: (str(d.index[0].date()), str(d.index[-1].date()), len(d)) for e, d in data.items()}
    for e, (s0, s1, nb) in spans.items():
        print(f"  {e}: {s0} -> {s1}  ({nb} bars)")
    rsi_parity_check(data)

    for hold in HOLDS:
        bcc, boc = base_rates(data, hold)
        print(f"\n  BASE RATE {hold}d hold: close->close up {bcc:.1f}% | next-open->close up {boc:.1f}%")

    verdicts = {}
    for variant in ["UP", "DOWN"]:
        for hold in HOLDS:
            print(f"\n{'-'*78}\n  VARIANT {'RSI2<10 -> UP' if variant=='UP' else 'RSI2>90 -> DOWN'}  hold={hold}d")
            for entry_mode in ["close", "next_open"]:
                for overlap in [True, False]:
                    all_trades = []
                    for etf, df in data.items():
                        all_trades += trades_for(df, hold, variant, entry_mode, overlap)
                    tag = f"{entry_mode}/{'overlap' if overlap else 'non-overlap'}"
                    print(fmt(metrics(all_trades), tag))
                    if entry_mode == "next_open" and not overlap:
                        # regime strata on the gate variant
                        bear = [t for t in all_trades if t[0].year in BEAR_YEARS]
                        bull = [t for t in all_trades if t[0].year not in BEAR_YEARS]
                        print(fmt(metrics(bear), "  bear years"))
                        print(fmt(metrics(bull), "  bull years"))
                        by_year = {}
                        for d, r in all_trades:
                            by_year.setdefault(d.year, []).append((d, r))
                        yline = "    per-year WR: " + " ".join(
                            f"{y}:{100*np.mean([x[1] > 0 for x in v]):.0f}%(n={len(v)})"
                            for y, v in sorted(by_year.items()))
                        print(yline)
                        if variant == "UP":
                            m_all, m_bear = metrics(all_trades), metrics(bear)
                            verdicts[hold] = {
                                "edge_pos": m_all["avg"] > 0,
                                "pf_ok": m_all["pf"] >= 1.0,
                                "bear_ok": (m_bear is None) or (m_bear["pf"] >= 0.8),
                                "m": m_all, "mb": m_bear,
                            }

    print(f"\n{'='*78}\n  TRIGGER PARITY (yfinance adj vs moomoo QFQ, recent overlap window)")
    moomoo_parity(data)

    print(f"\n{'='*78}\n  1a VERDICT (gate variant: RSI2<10 UP, next-open entry, non-overlapping)")
    overall = True
    for hold, v in verdicts.items():
        ok = v["edge_pos"] and v["pf_ok"] and v["bear_ok"]
        overall = overall and ok
        bear_note = f" (bear PF {v['mb']['pf']:.2f}, n={v['mb']['n']})" if v["mb"] else " (no bear-year trades)"
        print(f"  hold {hold}d: edge>0 {'PASS' if v['edge_pos'] else 'FAIL'} | "
              f"PF>=1 {'PASS' if v['pf_ok'] else 'FAIL'} (PF {v['m']['pf']:.2f}) | "
              f"bear PF>=0.8 {'PASS' if v['bear_ok'] else 'FAIL'}" + bear_note)
    print(f"\n  1a OVERALL: {'PASS' if overall else 'KILL'}")
    print("=" * 78)


if __name__ == "__main__":
    run()
