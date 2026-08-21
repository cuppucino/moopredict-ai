"""
Focus Engine — the info-first pipeline on a FIXED set of 4 US ETFs (kf, 2026-07-28).

Replaces the broad 57-ETF scan (which the backtest proved has no edge) with depth on a few
names. For each of SPY / QQQ / SMH / XLE, every day:

  Agent 1 (news/info)  — deterministic: gather news matched to the ETF + its real driver
                         (oil for XLE, chips/Asia for SMH, tech for QQQ, market for SPY) and
                         read net sentiment. -> a bullish/bearish signal in [-1, +1].
  Agent 2 (technical)  — PLAIN CODE (not an LLM — math can't fabricate): composite TA score +
                         RSI from ta_engine. -> a signal in [-1, +1].
  Combine              — start from the equity up-drift base rate (~0.54, the one robust edge)
                         and nudge with news + technicals -> a probability -> direction + CONFIDENCE.

Output per ETF: "SMH: 63% UP". Posts tagged [FOCUS]/category="focus", writes a daily briefing.
The news weight > technical weight on purpose: the backtest showed technicals barely beat a coin;
information is where the real edge is. Weights are provisional and get calibrated by backtest.
"""
from typing import Dict, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

from services.prediction_service import prediction_service
from services.sentiment_engine import sentiment_engine
from services.ta_engine import ta_engine
from services.political_monitor import TICKER_ALIASES
from core.database import SessionLocal, NewsIntel, Prediction

FOCUS_ETFS = ["SPY", "QQQ", "SMH", "XLE"]

# The real-world driver for each ETF — what we actually hunt news about (beyond the ticker).
DRIVERS = {
    "SPY": ["s&p", "stock market", "wall street", "fed", "rate cut", "inflation", "recession"],
    "QQQ": ["nasdaq", "big tech", "apple", "microsoft", "megacap", "ai stocks", "tech earnings"],
    "SMH": ["chip", "semiconductor", "sk hynix", "tsmc", "nvidia", "samsung", "memory", "ai chip"],
    "XLE": ["oil", "crude", "brent", "wti", "opec", "energy prices", "gas prices"],
}

# Per-ETF up-drift base rate. SPY/QQQ/XLE calibrated from ~1yr history and CONFIRMED stable
# across 5y/10y/full windows (scripts/validate_focus_priors.py, 2026-07-30). SMH recalibrated
# 2026-07-30: the original 0.595 existed only in the 1y/300-bar window (one semi bull run) —
# 10y=54.8 / 5y=53.8 / 3y=56.2 / 1y=59.4 -> multi-window blend 0.560. We were overconfident
# on SMH by ~4pp; validation output: scratch/openclaw_research_loop/FOCUS_VALIDATION_2026-07-30.txt
PRIORS = {"SPY": 0.548, "QQQ": 0.565, "SMH": 0.560, "XLE": 0.559}
DEFAULT_PRIOR = 0.54
# 2026-07-30 re-tune: at W_NEWS=0.16 the news could never overcome the priors — 11 of the
# first 12 focus calls were UP and two red days went 1/8. News is the only edge we have, so it
# must be ABLE to flip a call: at 0.30, a strongly bearish read (sig -0.5) pulls P(up) by -0.15,
# enough to take any prior below 0.5. Mildly bearish news still only dents confidence.
W_NEWS = 0.30
# Technicals scored 43-50% (WORSE than base) across every backtest — near-zero weight so the
# "technical agent" is present (per design) but can't drag the call. Dropped if it hurts live.
W_TECH = 0.04

INTEL_DIR = Path(__file__).resolve().parent.parent / "data" / "focus"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _log_decision(phase: str, etf: str, prior: float, news_sig: float, tech_sig: float,
                  p_up: float, direction: str, confidence: float, prediction_id=None,
                  news_det: str = "", tech_det: str = "", applied_change: str = ""):
    """Append one decision record to data/focus/decisions.jsonl (2026-07-30).

    This is how W_NEWS ever gets a verdict: the engine only adds value over the prior
    on evaluations where the signals flip (or nearly flip) the call away from the
    prior's always-UP default. Storing prior AND final P per evaluation makes those
    days measurable against resolved outcomes, and lets any weight setting be
    replayed offline. Every evaluation is logged, including intraday re-checks.
    Best-effort: logging must never break the engine.
    """
    try:
        import json
        INTEL_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "phase": phase,  # daily | intraday
            "etf": etf,
            "prior": round(prior, 4),
            "w_news": W_NEWS, "w_tech": W_TECH,
            "news_sig": round(news_sig, 4), "tech_sig": round(tech_sig, 4),
            "p_up": round(p_up, 4),
            "direction": direction, "confidence": confidence,
            "flip_vs_prior": (p_up < 0.5) != (prior < 0.5),
            "near_flip": abs(p_up - 0.5) <= 0.05,
            "prediction_id": prediction_id,
            "news_det": news_det, "tech_det": tech_det,
        }
        if applied_change:
            rec["applied_change"] = applied_change
        with open(INTEL_DIR / "decisions.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        logger.warning(f"[Focus] decision log failed for {etf}: {e}")


import re

# Word-boundary lexicon (audit 2026-08-13 C2-news): the old substring scan counted
# "against" as "gain", "surprise" as "rise", "circuit"/"mission" as bearish, and let
# BULL fire on routine copy — news_sig was positive in 93.2% of 512 evaluations and
# SPY/QQQ never once read bearish. All terms now match as whole words; phrases that
# invert polarity for equities are neutralized before counting.
_BULL_WORDS = ("surge", "surges", "rally", "rallies", "gains", "jumps", "rises", "beats",
               "soars", "climbs", "boosts", "upgrade", "upgraded", "rebound", "record high")
_BEAR_WORDS = ("plunge", "plunges", "drops", "falls", "slumps", "sinks", "misses",
               "sell-off", "selloff", "tumbles", "crashes", "downgrade", "downgraded",
               "warns", "recession", "record low")
# Bullish-for-equities phrases that contain bear words — removed from text pre-count.
_NEUTRALIZE = ("rate cut", "rate cuts", "tax cut", "tax cuts")
_BULL_RE = [re.compile(r"\b" + re.escape(w) + r"\b") for w in _BULL_WORDS]
_BEAR_RE = [re.compile(r"\b" + re.escape(w) + r"\b") for w in _BEAR_WORDS]


def _term_patterns(symbol: str) -> list:
    terms = [symbol] + list(TICKER_ALIASES.get(symbol, [])) + list(DRIVERS.get(symbol, []))
    return [re.compile(r"\b" + re.escape(t.strip().lower()) + r"\b") for t in terms if t.strip()]


def _news_signal(symbol: str) -> Tuple[float, str]:
    """Deterministic news read: sentiment on the ETF + driver-keyword news over 48h. [-1,+1]."""
    # base: the sentiment engine's per-symbol score (already lexicon-scored)
    try:
        s = sentiment_engine.score_symbol(symbol) or {}
        base = float(s.get("score", 0.0) or 0.0)
        # data_count on the populated path, count on the empty path (audit H1: reading
        # only "count" was always 0 and silently disarmed the coverage dampener).
        base_n = int(s.get("data_count", s.get("count", 0)) or 0)
    except Exception:
        base, base_n = 0.0, 0

    # driver news: scan 48h headlines for the ETF's aliases + driver terms (whole-word),
    # net bull/bear WORD counts. timestamptz column -> aware cutoff (exact 48h window).
    from datetime import timezone
    patterns = _term_patterns(symbol)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    db = SessionLocal()
    try:
        news = db.query(NewsIntel).filter(NewsIntel.scraped_at >= cutoff).all()
    finally:
        db.close()
    matched, bull, bear = 0, 0, 0
    for n in news:
        text = f"{n.headline or ''} {n.summary or ''}".lower()
        for phrase in _NEUTRALIZE:
            text = text.replace(phrase, " ")
        if any(p.search(text) for p in patterns):
            matched += 1
            bull += sum(bool(p.search(text)) for p in _BULL_RE)
            bear += sum(bool(p.search(text)) for p in _BEAR_RE)
    driver = ((bull - bear) / (bull + bear)) if (bull + bear) else 0.0

    # blend sentiment-engine score and driver polarity; scale confidence by how much we found
    raw = 0.6 * base + 0.4 * driver
    # Coverage dampener softened /4 -> /2 (2026-07-30): with /4 plus W_NEWS=0.16 the news never
    # once flipped a call. Two matched headlines now count at full strength; a single headline
    # still only half-counts (guards against one-story flips).
    coverage = min(1.0, (base_n + matched) / 2.0)
    sig = _clamp(raw * coverage, -1.0, 1.0)
    return sig, f"sent={base:+.2f}/{base_n} driver={driver:+.2f}({matched}n) -> {sig:+.2f}"


def _technical_signal(symbol: str) -> Tuple[float, str]:
    """PLAIN-CODE technical read from ta_engine: composite score + RSI. [-1,+1]."""
    try:
        a = ta_engine.get_full_analysis(symbol) or {}
    except Exception as e:
        return 0.0, f"TA error: {e}"
    if not a or a.get("price") is None:
        return 0.0, "no TA data"
    comp = float(a.get("composite_score", 0) or 0)
    rsi = float(a.get("rsi", 50) or 50)
    # composite_score is 0-100 (50 = neutral); map to [-1,+1].
    comp_sig = _clamp((comp - 50.0) / 50.0, -1.0, 1.0)
    # RSI: mild momentum tilt (>55 bullish, <45 bearish); extremes pull back toward mean-reversion
    rsi_sig = _clamp((rsi - 50) / 25.0, -1.0, 1.0)
    if rsi >= 75:
        rsi_sig = -0.4    # very overbought -> fade
    elif rsi <= 25:
        rsi_sig = 0.4     # very oversold -> bounce
    sig = _clamp(0.7 * comp_sig + 0.3 * rsi_sig, -1.0, 1.0)
    return sig, f"composite={comp:+.2f} rsi={rsi:.0f} -> {sig:+.2f}"


class FocusEngine:
    def generate(self) -> Dict:
        results, posted_ids = [], []
        for etf in FOCUS_ETFS:
            news_sig, news_det = _news_signal(etf)
            tech_sig, tech_det = _technical_signal(etf)
            prior = PRIORS.get(etf, DEFAULT_PRIOR)
            p = _clamp(prior + W_NEWS * news_sig + W_TECH * tech_sig, 0.05, 0.95)
            direction = "UP" if p >= 0.5 else "DOWN"
            confidence = round((p if direction == "UP" else 1 - p) * 100, 1)
            results.append({"etf": etf, "direction": direction, "confidence": confidence,
                            "p_up": round(p, 3), "news": news_det, "tech": tech_det})

            catalyst = (f"[FOCUS] {etf} {direction} @ {confidence}% — news[{news_det}] "
                        f"tech[{tech_det}] prior={prior} -> P(up)={p:.2f}")
            kw = dict(symbol=etf, direction=direction, confidence=confidence, catalyst=catalyst,
                      category="focus", timeframe_days=1, prediction_tag="TA_ONLY")
            res = prediction_service.create_prediction(force=False, **kw)
            if not res.get("success"):
                res = prediction_service.create_prediction(force=True, **kw)
            if res.get("success"):
                posted_ids.append(res["prediction_id"])
                logger.success(f"[Focus] #{res['prediction_id']} {etf} {direction} {confidence}%")
            _log_decision("daily", etf, prior, news_sig, tech_sig, p, direction, confidence,
                          prediction_id=res.get("prediction_id"),
                          news_det=news_det, tech_det=tech_det)

        try:
            INTEL_DIR.mkdir(parents=True, exist_ok=True)
            (INTEL_DIR / f"{datetime.utcnow().date().isoformat()}.md").write_text(self._render(results))
        except Exception as e:
            logger.error(f"[Focus] briefing write failed: {e}")

        logger.info(f"[Focus] {len(posted_ids)} posted: " +
                    ", ".join(f"{r['etf']} {r['direction']} {r['confidence']}%" for r in results))
        return {"success": bool(posted_ids), "posted": len(posted_ids), "ids": posted_ids, "results": results}

    def intraday_update(self) -> Dict:
        """Re-evaluate the 4 ETFs against FRESH news every 30 min during market hours. Keeps
        today's standing prediction reactive: if a signal has FLIPPED direction on breaking news,
        update that prediction in place + alert. One prediction per ETF/day — the final state at
        the deadline is what resolves — so scoring stays clean while the call stays current.

        Audit fixes 2026-08-13 (C3/H2): (1) no updates at/after 15:30 ET — a flip in the
        final 30 min (or after close: the one historical flip fired 16:30 ET) is graded on
        a day already in the books, look-ahead not forecast; (2) same-direction ticks no
        longer overwrite stored confidence — the DB keeps the value the call was MADE at,
        so Brier/conviction stats measure the real broadcast (jsonl still logs every tick);
        (3) flips append to the catalyst instead of replacing it (audit trail preserved)."""
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        if (now_et.hour, now_et.minute) >= (15, 30) or now_et.hour < 9:
            logger.info("[Focus] intraday: past 15:30 ET (or pre-market) — no updates, "
                        "day is effectively decided")
            return {"changes": [], "refreshed": 0, "note": "late-session guard"}
        changes, refreshed = [], 0
        for etf in FOCUS_ETFS:
            news_sig, news_det = _news_signal(etf)
            tech_sig, tech_det = _technical_signal(etf)
            prior = PRIORS.get(etf, DEFAULT_PRIOR)
            p = _clamp(prior + W_NEWS * news_sig + W_TECH * tech_sig, 0.05, 0.95)
            new_dir = "UP" if p >= 0.5 else "DOWN"
            new_conf = round((p if new_dir == "UP" else 1 - p) * 100, 1)
            db = SessionLocal()
            try:
                since = datetime.utcnow() - timedelta(hours=14)
                pred = (db.query(Prediction)
                        .filter_by(symbol=etf, category="focus", outcome=None)
                        .filter(Prediction.created_at >= since)
                        .order_by(Prediction.id.desc())
                        .first())
                if not pred:
                    _log_decision("intraday", etf, prior, news_sig, tech_sig, p, new_dir, new_conf,
                                  news_det=news_det, tech_det=tech_det)
                    continue
                if pred.direction != new_dir:
                    old = pred.direction
                    pred.direction, pred.confidence = new_dir, new_conf
                    # APPEND the revision — never destroy the original thesis.
                    pred.catalyst = (f"{pred.catalyst} || [intraday-revised {old}->{new_dir} "
                                     f"{datetime.utcnow():%H:%M}Z @ {new_conf}% — news[{news_det}] "
                                     f"tech[{tech_det}]]")
                    db.commit()
                    changes.append(f"{etf} {old}->{new_dir} @ {new_conf}%")
                    logger.warning(f"[Focus] INTRADAY FLIP {etf} {old}->{new_dir} on fresh news")
                    _log_decision("intraday", etf, prior, news_sig, tech_sig, p, new_dir, new_conf,
                                  prediction_id=pred.id, news_det=news_det, tech_det=tech_det,
                                  applied_change=f"{old}->{new_dir}")
                else:
                    # Same direction: DB row untouched — stored confidence stays what the
                    # call was made at. The tick is still fully logged to decisions.jsonl.
                    refreshed += 1
                    _log_decision("intraday", etf, prior, news_sig, tech_sig, p, new_dir, new_conf,
                                  prediction_id=pred.id, news_det=news_det, tech_det=tech_det)
            except Exception as e:
                logger.error(f"[Focus] intraday update {etf}: {e}")
            finally:
                db.close()
        if changes:
            try:
                from services.notifications import notification_queue
                notification_queue.enqueue(
                    f"🚨 *Focus revised on breaking news*\n" + "\n".join(f"• {c}" for c in changes),
                    "warning", category="news")
            except Exception:
                pass
        logger.info(f"[Focus] intraday: {len(changes)} flip(s), {refreshed} refreshed")
        return {"changes": changes, "refreshed": refreshed}

    def _render(self, results) -> str:
        out = [f"# Focus — {datetime.utcnow().date().isoformat()}",
               "> 4 US ETFs, info-first. news view + technical view -> confidence.", ""]
        for r in results:
            out.append(f"## {r['etf']}  →  **{r['direction']} @ {r['confidence']}%**")
            out.append(f"- news:      {r['news']}")
            out.append(f"- technical: {r['tech']}")
            out.append(f"- P(up) = {r['p_up']}")
            out.append("")
        return "\n".join(out)


focus_engine = FocusEngine()
