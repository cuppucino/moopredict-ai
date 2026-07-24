"""
Auto-Draft Engine — deterministic daily prediction generator.

Why this exists (2026-07-17): openclaw (the LLM agent) proved unreliable at the
mechanical act of POSTing predictions — it journals fabricated ledgers in memory
instead of writing to the database (fabricated #41-45 on Jul 13, #53 on Jul 17,
only Tue actually posted). No prompt fixed it; reliable execution is not an LLM
strength. So the SYSTEM now guarantees at least one real prediction per day,
deterministically, from the morning-brief scores + structure. openclaw's role
shrinks to reviewing/adjusting these — its actual strength (judgment).

Every draft is tagged [AUTO_DRAFT] in its catalyst so Phase 7 can separate the
deterministic baseline from openclaw's discretionary posts (when it does post).
"""
from typing import Dict
from loguru import logger

from services.morning_brief_engine import morning_brief_engine, get_cluster_for
from services.prediction_service import prediction_service

# Marker embedded in the catalyst string so the deterministic baseline is
# separable from openclaw's discretionary predictions in Phase 7 analysis.
AUTO_TAG = "[AUTO_DRAFT]"

# How many predictions to post per day. >1 speeds data velocity toward the next
# analysis checkpoint, but picks are chosen DIVERSIFIED across underlying clusters —
# 3 correlated bets (all tech-UP) aren't worth 3x the independent signal.
MAX_DRAFTS = 3


def _confidence_from_score(score: float):
    """Map brief score to an HONEST conviction tier. Floor at 40 (the system's
    minimum) so genuinely-low-conviction picks still post rather than get rejected —
    a low-conviction prediction honestly tagged is exactly the data Phase 7 needs."""
    if score >= 60:
        return 66.0, "HIGH"
    if score >= 40:
        return 52.0, "MEDIUM"
    return 42.0, "LOW"


# --- Phase 7 tilts (n=30, 2026-07-21). Modest & PROVISIONAL — buckets are ~n10.
# The auto-draft still posts daily; these only bias toward the conditions that have
# worked so far, and get re-checked as n grows. See PHASE7_RESULTS_2026-07-21.txt.
def _class_bonus(symbol: str) -> float:
    """Selection tilt by leverage class: sector ETFs carried WR 40%/PF 7.3; broad-index
    was 17% and thematic PF 0.28. Prefer sector, penalize the weak classes."""
    try:
        from services.prediction_service import classify_leverage
        lev = classify_leverage(symbol)
    except Exception:
        return 0.0
    return {"1x_sector": 15.0, "1x_index_broad": -12.0, "1x_thematic": -6.0}.get(lev, 0.0)


# Skip a leveraged single-stock ETF if its UNDERLYING reports within this many days —
# the underlying's earnings gap hits the ETF (leveraged, harder). 2026-07-24: GGLL (2x GOOGL)
# lost -14.4% on GOOGL's earnings drop; this gate would have skipped it.
EARNINGS_BLACKOUT_DAYS = 3


def _underlying_earnings_days(symbol: str):
    """For a leveraged single-stock ETF, days until its UNDERLYING stock reports (else None).
    Baskets/sector ETFs return None — they don't gap on one company's earnings."""
    from services.morning_brief_engine import get_cluster_for
    cluster = get_cluster_for(symbol)
    if not cluster or not cluster.endswith("_STOCK"):
        return None
    underlying = cluster[:-len("_STOCK")]
    try:
        from datetime import datetime
        from services.earnings_calendar import earnings_service
        dates = (earnings_service.get_stock_earnings(underlying) or {}).get("earnings_dates") or []
        if dates:
            return (datetime.strptime(str(dates[0])[:10], "%Y-%m-%d") - datetime.utcnow()).days
    except Exception:
        pass
    return None


def _regime_adjust(confidence: float, tier: str, regime: str):
    """Conviction tilt by regime: CHOP was 60% WR, trends ~0%. Dampen conviction one
    tier in trend regimes (we can't ride trends yet)."""
    if regime in ("TREND_UP", "TREND_DOWN"):
        return {"HIGH": (52.0, "MEDIUM"), "MEDIUM": (42.0, "LOW"), "LOW": (42.0, "LOW")}.get(
            tier, (confidence, tier))
    return confidence, tier


class AutoDraftEngine:
    def _select_diversified(self, brief: Dict, regime: str, n: int):
        """Pick up to n directional setups, ranked by Phase-7-adjusted score, at most
        ONE per underlying cluster (so we don't post 3 copies of the same bet). Returns a
        list of dicts: symbol, direction, score, confidence, tier. Falls back to a single
        regime-derived LOW-conviction pick if nothing has a real directional lean."""
        # Consider the whole universe, not just top5 — more room to diversify.
        scores = brief.get("all_scores") or {}
        cands = [{"ticker": t, **v} for t, v in scores.items()] if scores else list(brief.get("top5", []))
        directional = [c for c in cands if c.get("direction_lean") in ("UP", "DOWN")]

        if not directional:
            # Everything NEUTRAL — still guarantee ONE post from rank #1.
            top = brief.get("top5") or []
            if not top:
                return []
            return [{"ticker": top[0]["ticker"],
                     "direction": "DOWN" if regime == "TREND_DOWN" else "UP",
                     "score": top[0].get("score", 0) or 0, "confidence": 42.0, "tier": "LOW"}]

        directional.sort(key=lambda c: (c.get("score", 0) or 0) + _class_bonus(c["ticker"]), reverse=True)

        picks, seen_clusters = [], set()
        for c in directional:
            # Earnings gate: skip a leveraged single-stock ETF if its underlying reports soon.
            ed = _underlying_earnings_days(c["ticker"])
            if ed is not None and 0 <= ed <= EARNINGS_BLACKOUT_DAYS:
                logger.info(f"[AutoDraft] {c['ticker']} skipped — underlying earnings in {ed}d (blackout)")
                continue
            # One per cluster; tickers not in any cluster get a unique key (all eligible).
            key = get_cluster_for(c["ticker"]) or c["ticker"]
            if key in seen_clusters:
                continue
            seen_clusters.add(key)
            score = c.get("score", 0) or 0
            conf, tier = _regime_adjust(*_confidence_from_score(score), regime)
            picks.append({"ticker": c["ticker"], "direction": c["direction_lean"],
                          "score": score, "confidence": conf, "tier": tier})
            if len(picks) >= n:
                break
        return picks

    def _post_one(self, p: dict, regime: str) -> Dict:
        catalyst = (
            f"{AUTO_TAG} deterministic daily baseline — brief pick {p['ticker']}, "
            f"score={p['score']}, regime={regime}, conviction={p['tier']}. openclaw may review/adjust."
        )
        kw = dict(symbol=p["ticker"], direction=p["direction"], confidence=p["confidence"],
                  catalyst=catalyst, category="auto_draft", timeframe_days=1, prediction_tag="TA_ONLY")
        res = prediction_service.create_prediction(force=False, **kw)
        if not res.get("success"):
            logger.info(f"[AutoDraft] {p['ticker']} rejected by safety ({res.get('error')}) — forcing")
            res = prediction_service.create_prediction(force=True, **kw)
        if res.get("success"):
            logger.success(f"[AutoDraft] posted #{res['prediction_id']} {p['ticker']} {p['direction']} "
                           f"conf={p['confidence']} tier={p['tier']} entry={res.get('entry_price')}")
        else:
            logger.error(f"[AutoDraft] FAILED to post {p['ticker']}: {res.get('error')}")
        return res

    def generate_daily_draft(self) -> Dict:
        """Post up to MAX_DRAFTS diversified predictions/day (>=1 guaranteed). This is the
        reliable deterministic baseline openclaw can't provide. Picks are chosen by
        Phase-7-adjusted score, at most one per underlying cluster, conviction tagged
        honestly (regime-dampened in trends). Tagged [AUTO_DRAFT] for Phase 7 separation."""
        # Fresh pre-market scores; write_file=False so we don't clobber the 08:00 brief.
        try:
            brief = morning_brief_engine.generate_brief(write_file=False)
        except Exception as e:
            logger.error(f"[AutoDraft] brief generation failed: {e}")
            return {"success": False, "error": f"brief failed: {e}"}

        regime = brief.get("regime", {}).get("label", "CHOP")
        picks = self._select_diversified(brief, regime, MAX_DRAFTS)
        if not picks:
            logger.warning("[AutoDraft] brief returned no candidates — cannot draft today")
            return {"success": False, "error": "empty brief"}

        results = [self._post_one(p, regime) for p in picks]
        posted = [r for r in results if r.get("success")]
        logger.info(f"[AutoDraft] daily run complete — {len(posted)}/{len(picks)} posted "
                    f"({', '.join(p['ticker'] for p in picks)})")
        return {"success": bool(posted), "posted": len(posted),
                "ids": [r.get("prediction_id") for r in posted]}


auto_draft_engine = AutoDraftEngine()
