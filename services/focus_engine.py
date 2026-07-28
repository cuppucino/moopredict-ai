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
from core.database import SessionLocal, NewsIntel

FOCUS_ETFS = ["SPY", "QQQ", "SMH", "XLE"]

# The real-world driver for each ETF — what we actually hunt news about (beyond the ticker).
DRIVERS = {
    "SPY": ["s&p", "stock market", "wall street", "fed", "rate cut", "inflation", "recession"],
    "QQQ": ["nasdaq", "big tech", "apple", "microsoft", "megacap", "ai stocks", "tech earnings"],
    "SMH": ["chip", "semiconductor", "sk hynix", "tsmc", "nvidia", "samsung", "memory", "ai chip"],
    "XLE": ["oil", "crude", "brent", "wti", "opec", "energy prices", "gas prices"],
}

UP_DRIFT_PRIOR = 0.54    # ~54% of equity days are up — the base rate to start from
W_NEWS = 0.14            # news moves the needle more than TA (backtest: TA ~= coin flip)
W_TECH = 0.09

INTEL_DIR = Path(__file__).resolve().parent.parent / "data" / "focus"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _news_signal(symbol: str) -> Tuple[float, str]:
    """Deterministic news read: sentiment on the ETF + driver-keyword news over 48h. [-1,+1]."""
    # base: the sentiment engine's per-symbol score (already lexicon-scored)
    try:
        s = sentiment_engine.score_symbol(symbol) or {}
        base = float(s.get("score", 0.0) or 0.0)
        base_n = int(s.get("count", 0) or 0)
    except Exception:
        base, base_n = 0.0, 0

    # driver news: scan 48h headlines for the ETF's aliases + driver terms, net bull/bear words
    terms = [symbol.lower()] + [a.lower() for a in TICKER_ALIASES.get(symbol, [])] + DRIVERS.get(symbol, [])
    BULL = ("surge", "rally", "gain", "jump", "rise", "beat", "record", "soar", "up ", "climb", "boost", "upgrade")
    BEAR = ("plunge", "drop", "fall", "slump", "sink", "miss", "sell-off", "selloff", "tumble", "crash", "cut", "downgrade", "bear", "warn")
    cutoff = datetime.utcnow() - timedelta(hours=48)
    db = SessionLocal()
    try:
        news = db.query(NewsIntel).filter(NewsIntel.scraped_at >= cutoff).all()
    finally:
        db.close()
    matched, bull, bear = 0, 0, 0
    for n in news:
        text = f"{n.headline or ''} {n.summary or ''}".lower()
        if any(t in text for t in terms):
            matched += 1
            bull += sum(w in text for w in BULL)
            bear += sum(w in text for w in BEAR)
    driver = ((bull - bear) / (bull + bear)) if (bull + bear) else 0.0

    # blend sentiment-engine score and driver polarity; scale confidence by how much we found
    raw = 0.6 * base + 0.4 * driver
    coverage = min(1.0, (base_n + matched) / 4.0)   # thin coverage -> weak signal
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
            p = _clamp(UP_DRIFT_PRIOR + W_NEWS * news_sig + W_TECH * tech_sig, 0.05, 0.95)
            direction = "UP" if p >= 0.5 else "DOWN"
            confidence = round((p if direction == "UP" else 1 - p) * 100, 1)
            results.append({"etf": etf, "direction": direction, "confidence": confidence,
                            "p_up": round(p, 3), "news": news_det, "tech": tech_det})

            catalyst = (f"[FOCUS] {etf} {direction} @ {confidence}% — news[{news_det}] "
                        f"tech[{tech_det}] prior={UP_DRIFT_PRIOR} -> P(up)={p:.2f}")
            kw = dict(symbol=etf, direction=direction, confidence=confidence, catalyst=catalyst,
                      category="focus", timeframe_days=1, prediction_tag="TA_ONLY")
            res = prediction_service.create_prediction(force=False, **kw)
            if not res.get("success"):
                res = prediction_service.create_prediction(force=True, **kw)
            if res.get("success"):
                posted_ids.append(res["prediction_id"])
                logger.success(f"[Focus] #{res['prediction_id']} {etf} {direction} {confidence}%")

        try:
            INTEL_DIR.mkdir(parents=True, exist_ok=True)
            (INTEL_DIR / f"{datetime.utcnow().date().isoformat()}.md").write_text(self._render(results))
        except Exception as e:
            logger.error(f"[Focus] briefing write failed: {e}")

        logger.info(f"[Focus] {len(posted_ids)} posted: " +
                    ", ".join(f"{r['etf']} {r['direction']} {r['confidence']}%" for r in results))
        return {"success": bool(posted_ids), "posted": len(posted_ids), "ids": posted_ids, "results": results}

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
