"""
Phase 7 analysis harness — the n>=30 read on whether the system has edge.

Answers, from real resolved predictions:
  - Overall: n, win rate, profit factor (and PF with the IBM single-stock outlier removed)
  - WR by regime / leverage class / tag / direction / structure confluence
  - WR by structure signal present (capitulation, demand_zone, fvg, ...)
  - The threshold question: how many WRONGs were direction-right sub-0.3%, and WR at 0.1/0.2/0.3/0.5%
  - STOCK vs ETF (isolating single-stock event risk)
  - AUTO_DRAFT (deterministic baseline) vs openclaw discretionary

Run: python3 scripts/phase7_analysis.py
Read-only. Safe to run any time.
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.database import SessionLocal, Prediction

try:
    from services.morning_brief_engine import ETF_UNIVERSE
    ETF_SET = set(ETF_UNIVERSE)
except Exception:
    ETF_SET = set()


def directional_return(p):
    """Signed % return of the call: +move if UP call, -move if DOWN call.
    Non-directional (WAIT/FLAT) calls contribute 0 to PF."""
    m = p.actual_move_pct
    d = (p.direction or "").upper()
    if m is None or d not in ("UP", "DOWN"):
        return 0.0
    return m if d == "UP" else -m


def wr(rows):
    if not rows:
        return (0, 0.0)
    right = sum(1 for p in rows if p.outcome == "RIGHT")
    return (len(rows), right / len(rows))


def pf(rows):
    gains = sum(directional_return(p) for p in rows if directional_return(p) > 0)
    losses = sum(-directional_return(p) for p in rows if directional_return(p) < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def line(label, rows, extra=""):
    n, w = wr(rows)
    if n == 0:
        return f"  {label:28} n=0"
    return f"  {label:28} n={n:<3} WR={w*100:5.1f}%  PF={pf(rows):5.2f}  {extra}"


def group_report(title, rows, keyfn):
    print(f"\n{title}")
    buckets = defaultdict(list)
    for p in rows:
        buckets[keyfn(p)].append(p)
    for k in sorted(buckets, key=lambda k: (-len(buckets[k]), str(k))):
        print(line(str(k), buckets[k]))


def report(rows, cohort_label):
    print("\n" + "=" * 64)
    print(f"  PHASE 7 ANALYSIS — {cohort_label} — {len(rows)} resolved predictions")
    print("=" * 64)
    if not rows:
        print("  (no resolved predictions in this cohort)")
        return

    # ---- Overall ----
    n, w = wr(rows)
    print(f"\nOVERALL:  n={n}  WR={w*100:.1f}%  PF={pf(rows):.2f}")

    # IBM outlier isolation
    stocks = [p for p in rows if p.symbol not in ETF_SET]
    etfs = [p for p in rows if p.symbol in ETF_SET]
    print(f"  ETF-only (single stocks removed): {line('', etfs).strip()}")
    print(f"  Single stocks:                    {line('', stocks).strip()}")
    if stocks:
        print(f"  -> stock outlier(s): " + ", ".join(
            f"{p.symbol} {p.direction} {directional_return(p):+.1f}%" for p in stocks))

    # ---- The threshold question ----
    print("\nTHRESHOLD QUESTION (were 'WRONG' calls direction-right sub-threshold?):")
    wrongs = [p for p in rows if p.outcome == "WRONG"]
    for pct, attr in [("0.1%", "would_be_right_at_01pct"), ("0.2%", "would_be_right_at_02pct"),
                      ("0.3%", "would_be_right_at_03pct"), ("0.5%", "would_be_right_at_05pct")]:
        flagged = [p for p in rows if getattr(p, attr) is True]
        n_all, _ = wr(rows)
        wr_at = (len(flagged) / n_all * 100) if n_all else 0
        wrongs_saved = sum(1 for p in wrongs if getattr(p, attr) is True)
        print(f"  @ {pct:5} would-be-RIGHT count={len(flagged):<3} => WR {wr_at:5.1f}%   "
              f"({wrongs_saved} current WRONGs were dir-right at this bar)")

    # ---- Dimension breakdowns ----
    group_report("BY REGIME AT OPEN:", rows, lambda p: p.regime_at_open or "UNKNOWN")
    group_report("BY LEVERAGE CLASS:", rows, lambda p: p.leverage_class or "unknown")
    group_report("BY TAG:", rows, lambda p: p.prediction_tag or "none")
    group_report("BY DIRECTION:", rows, lambda p: p.direction)
    group_report("BY STRUCTURE CONFLUENCE:", rows,
                 lambda p: f"confluence={p.structure_confluence if p.structure_confluence is not None else '?'}")

    # ---- Source: auto-draft baseline vs openclaw discretionary ----
    def source(p):
        cat = p.catalyst or ""
        if "[SELECTIVE]" in cat:
            return "SELECTIVE (high-conviction)"
        if "[AUTO_DRAFT]" in cat:
            return "AUTO_DRAFT (baseline)"
        return "openclaw (discretionary)"
    group_report("BY SOURCE (baseline vs discretionary):", rows, source)

    # ---- WR when a given structure signal was present ----
    print("\nBY STRUCTURE SIGNAL PRESENT:")
    sig_rows = defaultdict(list)
    for p in rows:
        sigs = p.structure_signals if isinstance(p.structure_signals, list) else []
        for s in sigs:
            sig_rows[s].append(p)
    if not sig_rows:
        print("  (no structure signals stamped on resolved predictions yet)")
    for s in sorted(sig_rows, key=lambda s: -len(sig_rows[s])):
        print(line(s, sig_rows[s]))

    print("\n  Notes: PF = sum(win %) / sum(loss %), directional. n<5 buckets are noise.")


def main():
    db = SessionLocal()
    try:
        every = db.query(Prediction).filter(Prediction.outcome.in_(["RIGHT", "WRONG"])).all()
    finally:
        db.close()

    # Split the informational position-watch track out of the ETF-experiment cohorts —
    # single-stock reads on the user's holdings, measured on their own, never pooled.
    poswatch = [p for p in every if p.category == "position_watch"]
    allrows = [p for p in every if p.category != "position_watch"]

    cutoff = datetime.utcnow() - timedelta(days=30)
    win30 = [p for p in allrows if p.resolved_at and p.resolved_at >= cutoff]
    # "Clean era" = predictions carrying instrumentation (regime stamped since Phase 6+).
    instrumented = [p for p in allrows if p.regime_at_open and p.regime_at_open != "UNKNOWN"]

    # Headline cohort = 30-day window (what the gate + track record use).
    report(win30, "LAST 30 DAYS (gate cohort)")
    # Secondary = instrumented-only (drops pre-Phase-6 noise for the dimension cuts).
    report(instrumented, "INSTRUMENTED ONLY (regime-stamped)")
    # Context = all-time.
    report(allrows, "ALL-TIME (context)")
    # The informational position-watch track, measured entirely separately.
    if poswatch:
        report(poswatch, "POSITION-WATCH (informational, held stocks — separate)")


if __name__ == "__main__":
    main()
