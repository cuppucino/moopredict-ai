"""
Focus validation — the 4 focus ETFs only (SPY/QQQ/SMH/XLE).

Tests the foundation of the focus engine (services/focus_engine.py):

  PRIORS = SPY .548 | QQQ .565 | SMH .595 | XLE .559   (calibrated on ~300d, 2026-07)

Questions answered:
  1. STABILITY — are those priors durable properties of these ETFs, or artifacts of
     the ~300-day (bullish) calibration window? Up-rates across 2000-present, 10y,
     5y, 3y, 1y, 300d, plus per-year for the last decade, plus bear-year strata.
  2. THE +0.3% GAP — live resolution scores UP as RIGHT only if move > +0.3%
     (prediction_service THRESHOLD). The priors are RAW up-day rates; the rate the
     live track can actually achieve with always-UP is the @+0.3% rate, which is
     several points lower. Quantify the gap per ETF (and the NEUTRAL zone share).
  3. LIVE TRACK — what the focus engine has actually scored so far (category=focus),
     and whether it has yet made any call a prior-only baseline would not have made
     (it only adds value over the prior when it flips a call to DOWN or abstains —
     confidence tilts don't change RIGHT/WRONG).

Read-only. yfinance auto_adjust=True pinned. Skips the DB section gracefully.
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ETFS = ["SPY", "QQQ", "SMH", "XLE"]
CONFIGURED_PRIORS = {"SPY": 0.548, "QQQ": 0.565, "SMH": 0.595, "XLE": 0.559}
THRESH = 0.003  # live RIGHT bar for UP (prediction_service THRESHOLD = 0.3%)
BEAR_YEARS = {2008, 2011, 2015, 2018, 2020, 2022}


def fetch(etfs):
    import yfinance as yf
    raw = yf.download(etfs, start="2000-01-01", auto_adjust=True, progress=False, group_by="ticker")
    out = {}
    for etf in etfs:
        df = raw[etf].dropna(subset=["Close"]).copy()
        df.columns = [c.lower() for c in df.columns]
        out[etf] = df
    return out


def rates(closes: pd.Series):
    """(raw up-rate, rate@+0.3%, neutral share |move|<=0.3%, rate@-0.3% down) for 1d moves."""
    mv = closes.pct_change().dropna()
    if len(mv) == 0:
        return None
    return (100 * (mv > 0).mean(), 100 * (mv > THRESH).mean(),
            100 * (mv.abs() <= THRESH).mean(), 100 * (mv < -THRESH).mean(), len(mv))


def window_table(data):
    now = data["SPY"].index[-1]
    windows = [
        ("full 2000-", None),
        ("10y", now - pd.DateOffset(years=10)),
        ("5y", now - pd.DateOffset(years=5)),
        ("3y", now - pd.DateOffset(years=3)),
        ("1y", now - pd.DateOffset(years=1)),
        ("300 bars", "300bars"),
    ]
    print(f"\n  {'window':<12}" + "".join(f"{e:>22}" for e in ETFS))
    print(f"  {'':<12}" + "".join(f"{'raw% / @0.3% / n':>22}" for _ in ETFS))
    for label, start in windows:
        cells = []
        for etf in ETFS:
            c = data[etf]["close"]
            if start == "300bars":
                c = c.iloc[-301:]
            elif start is not None:
                c = c[c.index >= start]
            r = rates(c)
            cells.append(f"{r[0]:5.1f} /{r[1]:5.1f} /{r[4]:>5}" if r else "n/a")
        print(f"  {label:<12}" + "".join(f"{x:>22}" for x in cells))
    print(f"\n  configured  " + "".join(f"{100*CONFIGURED_PRIORS[e]:>16.1f}%     " for e in ETFS))


def per_year(data, since=2015):
    print(f"\n  per-year RAW up-rate (since {since}):")
    years = sorted({y for y in data["SPY"].index.year if y >= since})
    print(f"  {'year':<6}" + "".join(f"{e:>8}" for e in ETFS))
    for y in years:
        row = []
        for etf in ETFS:
            c = data[etf]["close"][data[etf].index.year == y]
            r = rates(c)
            row.append(f"{r[0]:7.1f}" if r else "    n/a")
        tag = "  <- bear" if y in BEAR_YEARS else ""
        print(f"  {y:<6}" + "".join(row) + tag)
    print("\n  bear-years vs bull-years RAW up-rate (full sample):")
    for etf in ETFS:
        c = data[etf]["close"]
        bear = c[[y in BEAR_YEARS for y in c.index.year]]
        bull = c[[y not in BEAR_YEARS for y in c.index.year]]
        rb, rl = rates(bear), rates(bull)
        print(f"    {etf}: bear {rb[0]:.1f}% (@0.3% {rb[1]:.1f}%)  |  bull {rl[0]:.1f}% (@0.3% {rl[1]:.1f}%)")


def live_track():
    print(f"\n{'='*78}\n  LIVE FOCUS TRACK (category=focus)")
    try:
        from core.database import SessionLocal, Prediction
        db = SessionLocal()
        preds = db.query(Prediction).filter(Prediction.category == "focus").order_by(Prediction.id).all()
        res = [p for p in preds if p.outcome]
        if not preds:
            print("  no focus predictions in DB")
            return
        n_right = sum(1 for p in res if p.outcome == "RIGHT")
        n_down_calls = sum(1 for p in preds if p.direction == "DOWN")
        print(f"  total {len(preds)} | resolved {len(res)} | RIGHT {n_right}/{len(res)}"
              f" ({100*n_right/max(1,len(res)):.0f}%) | DOWN calls made: {n_down_calls}")
        by_etf = {}
        for p in res:
            by_etf.setdefault(p.symbol, []).append(p)
        for etf, ps in sorted(by_etf.items()):
            r = sum(1 for p in ps if p.outcome == "RIGHT")
            moves = [p.actual_move_pct for p in ps if p.actual_move_pct is not None]
            print(f"    {etf}: {r}/{len(ps)} RIGHT, moves: " +
                  ", ".join(f"{m:+.2f}%" for m in moves))
        if n_down_calls == 0:
            print("  NOTE: zero DOWN calls so far -> live focus == always-UP prior baseline")
            print("        by construction. W_NEWS/W_TECH have only tilted CONFIDENCE, never")
            print("        a direction. The engine has not yet had a chance to add value over")
            print("        the prior; its value-add is measured ONLY on days it flips or would")
            print("        flip. Track those separately before judging W_NEWS.")
        db.close()
    except Exception as e:
        print(f"  DB unavailable ({type(e).__name__}: {e}) — live section skipped")


def run():
    print("=" * 78)
    print("  FOCUS VALIDATION — SPY / QQQ / SMH / XLE only")
    print("=" * 78)
    data = fetch(ETFS)
    for etf in ETFS:
        d = data[etf]
        print(f"  {etf}: {d.index[0].date()} -> {d.index[-1].date()} ({len(d)} bars)")

    print(f"\n  1) PRIOR STABILITY — raw 1d up-rate / rate under the live +0.3% RIGHT bar")
    window_table(data)
    per_year(data)

    print(f"\n  2) THE +0.3% GAP (full sample): what always-UP can actually score live")
    for etf in ETFS:
        r = rates(data[etf]["close"])
        print(f"    {etf}: raw up {r[0]:.1f}%  ->  @+0.3% only {r[1]:.1f}%  "
              f"(neutral zone |move|<=0.3%: {r[2]:.1f}% of days)")

    live_track()

    print(f"\n{'='*78}\n  VERDICT NOTES (auto-computed facts above; read with these lenses)")
    print("  - A configured prior is VALIDATED only if the raw up-rate is stable across")
    print("    5y/3y/1y windows, not just the 300-bar calibration window.")
    print("  - The live track scores at +0.3%: expected always-UP RIGHT-rate is the @0.3%")
    print("    column, NOT the prior. Judge the live WR against THAT number.")
    print("  - Bear-year rows show what happens to always-UP when the regime turns.")
    print("=" * 78)


if __name__ == "__main__":
    run()
