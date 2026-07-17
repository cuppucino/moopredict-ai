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

from services.morning_brief_engine import morning_brief_engine
from services.prediction_service import prediction_service

# Marker embedded in the catalyst string so the deterministic baseline is
# separable from openclaw's discretionary predictions in Phase 7 analysis.
AUTO_TAG = "[AUTO_DRAFT]"


def _confidence_from_score(score: float):
    """Map brief score to an HONEST conviction tier. Floor at 40 (the system's
    minimum) so genuinely-low-conviction picks still post rather than get rejected —
    a low-conviction prediction honestly tagged is exactly the data Phase 7 needs."""
    if score >= 60:
        return 66.0, "HIGH"
    if score >= 40:
        return 52.0, "MEDIUM"
    return 42.0, "LOW"


class AutoDraftEngine:
    def generate_daily_draft(self) -> Dict:
        """Post AT LEAST ONE real prediction to the database from the day's best
        brief setup. Guaranteed — this is the reliable baseline openclaw can't provide.

        Selection: highest-scored ETF whose news-driven direction_lean is not NEUTRAL.
        If every top pick is NEUTRAL, fall back to rank #1 with a regime-derived
        direction, tagged LOW conviction (no real directional signal)."""
        # Fresh pre-market scores; write_file=False so we don't clobber the 08:00 brief.
        try:
            brief = morning_brief_engine.generate_brief(write_file=False)
        except Exception as e:
            logger.error(f"[AutoDraft] brief generation failed: {e}")
            return {"success": False, "error": f"brief failed: {e}"}

        top5 = brief.get("top5", [])
        regime = brief.get("regime", {}).get("label", "CHOP")
        if not top5:
            logger.warning("[AutoDraft] brief returned no ETFs — cannot draft today")
            return {"success": False, "error": "empty brief"}

        # Prefer the highest-scored setup with a real directional lean.
        pick = next((e for e in top5 if e.get("direction_lean") in ("UP", "DOWN")), None)
        if pick is not None:
            direction = pick["direction_lean"]
            score = pick.get("score", 0) or 0
            confidence, tier = _confidence_from_score(score)
        else:
            # Everything NEUTRAL — still guarantee a post. Take rank #1, derive a
            # direction from regime, tag LOW (there is no genuine directional signal).
            pick = top5[0]
            direction = "DOWN" if regime == "TREND_DOWN" else "UP"
            score = pick.get("score", 0) or 0
            confidence, tier = 42.0, "LOW"

        symbol = pick["ticker"]
        catalyst = (
            f"{AUTO_TAG} deterministic daily baseline — brief pick {symbol}, "
            f"score={score}, lean={pick.get('direction_lean')}, regime={regime}, "
            f"conviction={tier}. openclaw may review/adjust."
        )

        # Try clean first (respects cluster/duplicate safety); if that rejects,
        # force so the daily post is still guaranteed.
        res = prediction_service.create_prediction(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            catalyst=catalyst,
            category="auto_draft",
            timeframe_days=1,
            prediction_tag="TA_ONLY",
            force=False,
        )
        if not res.get("success"):
            logger.info(f"[AutoDraft] {symbol} rejected by safety ({res.get('error')}) — forcing to guarantee daily post")
            res = prediction_service.create_prediction(
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                catalyst=catalyst,
                category="auto_draft",
                timeframe_days=1,
                prediction_tag="TA_ONLY",
                force=True,
            )

        if res.get("success"):
            logger.success(
                f"[AutoDraft] posted #{res['prediction_id']} {symbol} {direction} "
                f"conf={confidence} tier={tier} score={score} entry={res.get('entry_price')}"
            )
        else:
            logger.error(f"[AutoDraft] FAILED to post daily draft for {symbol}: {res.get('error')}")
        return res


auto_draft_engine = AutoDraftEngine()
