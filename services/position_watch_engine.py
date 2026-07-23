"""
Position-Watch Engine — daily predictions on the user's ACTUAL held positions.

kf's request (2026-07-23): a focused agent that watches only what he holds, studies each
with the system's algorithms (structure + TA + news/sentiment), and predicts direction daily
— narrow scope (his positions), not the whole 57-ETF universe.

Built as a DETERMINISTIC engine, not an LLM agent — same reliability lesson as the auto-draft
(openclaw proved LLM agents unreliable at the mechanical POST). The "studying" IS the algorithms.

Its own track, tagged [POSITION_WATCH] / category="position_watch", kept separate from the ETF
experiment (held positions are single stocks with earnings-gap event risk). The Phase 7 harness
splits by tag, so this never distorts the ETF read.

IMPORTANT: these use the SAME not-yet-proven algorithms (ETF WR ~33%), so they are INFORMATIONAL
("what does the system think about your holdings today"), NOT trade signals.
"""
from typing import Dict
from loguru import logger

from services.moomoo_service import moomoo_service
from services.morning_brief_engine import morning_brief_engine
from services.prediction_service import prediction_service, classify_regime

POS_TAG = "[POSITION_WATCH]"


def _bare(symbol: str) -> str:
    """'US.NVDA' -> 'NVDA'."""
    return (symbol or "").split(".")[-1].strip().upper()


class PositionWatchEngine:
    def _structure_lean(self, symbol: str):
        """Per-stock TECHNICAL direction from the structure engine, for when news is quiet
        (individual stocks rarely have 24h news signal). Returns 'UP' / 'DOWN' / None.
        Reads capitulation (mean-reversion: down-panic -> bounce UP, blow-off -> fade DOWN)
        then break-of-structure trend."""
        try:
            from services.structure_engine import structure_engine
            s = structure_engine.get_structure(symbol)
        except Exception:
            return None
        if not s or s.get("error"):
            return None
        # 1) Capitulation (rare, strong): down-panic -> mean-reversion UP, blow-off -> DOWN.
        cap = s.get("capitulation", {}) or {}
        if cap.get("detected"):
            return "UP" if cap.get("type") == "down_capitulation" else "DOWN"
        # 2) Break of structure (rare): trend confirmation.
        bos = s.get("bos", {}) or {}
        if bos.get("direction") == "bullish":
            return "UP"
        if bos.get("direction") == "bearish":
            return "DOWN"
        # 3) Everyday trend read (almost always available): the recent-swing direction, so
        #    each holding gets a real technical lean instead of falling back to regime-default.
        fib = s.get("fib", {}) or {}
        if fib.get("available") and fib.get("direction") in ("up", "down"):
            return "UP" if fib["direction"] == "up" else "DOWN"
        return None

    def generate_daily(self) -> Dict:
        """Predict direction on every currently-held position, once per day."""
        try:
            positions = moomoo_service.get_positions() or []
        except Exception as e:
            logger.error(f"[PositionWatch] get_positions failed: {e}")
            return {"success": False, "error": f"positions: {e}"}

        symbols = sorted({_bare(p.get("symbol", "")) for p in positions if p.get("symbol")})
        symbols = [s for s in symbols if s]
        if not symbols:
            logger.info("[PositionWatch] no open positions — nothing to predict")
            return {"success": True, "posted": 0, "ids": [], "note": "no positions"}

        try:
            scores = morning_brief_engine.score_symbols(symbols)
        except Exception as e:
            logger.error(f"[PositionWatch] scoring failed: {e}")
            return {"success": False, "error": f"scoring: {e}"}
        regime = classify_regime()

        posted_ids = []
        for sym in symbols:
            sc = scores.get(sym, {}) or {}
            score = sc.get("score", 0) or 0
            lean = sc.get("direction_lean", "NEUTRAL")
            # Direction priority: news lean -> structure/TA read -> regime fallback.
            if lean in ("UP", "DOWN"):
                direction, source = lean, "news"
                confidence = 52.0 if score >= 40 else 44.0
                tier = "MEDIUM" if score >= 40 else "LOW"
            else:
                struct = self._structure_lean(sym)
                if struct:
                    direction, source, confidence, tier = struct, "structure", 48.0, "MEDIUM"
                else:
                    direction = "DOWN" if regime == "TREND_DOWN" else "UP"
                    source, confidence, tier = "regime", 42.0, "LOW"

            catalyst = (
                f"{POS_TAG} held-position daily read — {sym}, direction from {source} "
                f"(news score={score}, lean={lean}), regime={regime}, conviction={tier}. "
                f"Informational (system algorithms on a held position), NOT a trade signal."
            )
            kw = dict(symbol=sym, direction=direction, confidence=confidence, catalyst=catalyst,
                      category="position_watch", timeframe_days=1, prediction_tag="TA_ONLY")
            res = prediction_service.create_prediction(force=False, **kw)
            if not res.get("success"):
                res = prediction_service.create_prediction(force=True, **kw)
            if res.get("success"):
                posted_ids.append(res["prediction_id"])
                logger.success(f"[PositionWatch] posted #{res['prediction_id']} {sym} {direction} "
                               f"conf={confidence} tier={tier} score={score}")
            else:
                logger.error(f"[PositionWatch] FAILED to post {sym}: {res.get('error')}")

        logger.info(f"[PositionWatch] daily run — {len(posted_ids)}/{len(symbols)} posted "
                    f"({', '.join(symbols)})")
        return {"success": bool(posted_ids), "posted": len(posted_ids),
                "ids": posted_ids, "symbols": symbols}


position_watch_engine = PositionWatchEngine()
