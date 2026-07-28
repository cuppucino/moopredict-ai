"""
Selective Engine — the HIGH-CONVICTION track. The accuracy play (kf, 2026-07-28).

Phase 7 (n=30-40) showed win rate swings wildly by CONDITION, not by luck:
  CHOP regime 60% vs trends ~0% · sector ETFs 40%/PF7.3 vs broad-index 17%/thematic 0.28 ·
  structure confluence helps.
So instead of predicting on everything (that's the auto-draft's job — data velocity), this
fires ONLY when the proven-good conditions STACK. It trades volume for hit-rate. Some days it
posts nothing — that is the entire point: accuracy is a FILTERING problem, not a crystal ball.

The gate:
  HARD: regime must be CHOP (Phase 7: the edge lives there; trends were ~0%).
  THEN require >= 2 of 3 quality signals: sector-ETF · structure confluence>=1 · brief score>=45.
  PLUS the earnings blackout (reused from auto-draft) so it never buys into an earnings gap.

Tagged [SELECTIVE] / category="selective", measured on its own in Phase 7. Goal: prove >55-60%
WR on this filtered slice, where the broad book sits at ~37%. If it can't, selectivity failed —
and that's a real finding too.
"""
from typing import Dict
from loguru import logger

from services.morning_brief_engine import morning_brief_engine, get_cluster_for
from services.prediction_service import prediction_service, classify_leverage
from services.auto_draft_engine import _underlying_earnings_days, EARNINGS_BLACKOUT_DAYS

SEL_TAG = "[SELECTIVE]"
MAX_SELECTIVE = 2       # never spray — selectivity is the whole idea
EVAL_TOP_N = 8          # only structure-check the top candidates (structure calls are slow)


def _structure_confluence(symbol: str) -> int:
    try:
        from services.structure_engine import structure_engine
        s = structure_engine.get_structure(symbol)
        if s and not s.get("error"):
            return int(s.get("confluence", {}).get("score", 0) or 0)
    except Exception:
        pass
    return 0


class SelectiveEngine:
    def generate(self) -> Dict:
        try:
            brief = morning_brief_engine.generate_brief(write_file=False)
        except Exception as e:
            logger.error(f"[Selective] brief failed: {e}")
            return {"success": False, "error": str(e)}
        regime = brief.get("regime", {}).get("label", "UNKNOWN")

        # HARD GATE — Phase 7: the edge is in CHOP. Anything else, sit out entirely.
        if regime != "CHOP":
            logger.info(f"[Selective] regime={regime} (not CHOP) — sitting out")
            return {"success": True, "posted": 0, "ids": [], "note": f"regime {regime}, sat out"}

        cands = [{"ticker": t, **v} for t, v in (brief.get("all_scores") or {}).items()]
        directional = [c for c in cands if c.get("direction_lean") in ("UP", "DOWN")]
        directional.sort(key=lambda c: c.get("score", 0) or 0, reverse=True)

        picks, seen = [], set()
        for c in directional[:EVAL_TOP_N]:
            sym = c["ticker"]
            ed = _underlying_earnings_days(sym)
            if ed is not None and 0 <= ed <= EARNINGS_BLACKOUT_DAYS:
                logger.info(f"[Selective] {sym} skipped — underlying earnings in {ed}d")
                continue
            lev = classify_leverage(sym)
            score = c.get("score", 0) or 0
            conf = _structure_confluence(sym)
            quality = int(lev == "1x_sector") + int(conf >= 1) + int(score >= 45)
            if quality < 2:
                continue
            key = get_cluster_for(sym) or sym
            if key in seen:
                continue
            seen.add(key)
            picks.append({"ticker": sym, "direction": c["direction_lean"],
                          "score": score, "conf": conf, "lev": lev, "quality": quality})
            if len(picks) >= MAX_SELECTIVE:
                break

        if not picks:
            logger.info("[Selective] CHOP but nothing cleared the 2-of-3 quality gate — no post today")
            return {"success": True, "posted": 0, "ids": [], "note": "no high-conviction setup"}

        posted = []
        for p in picks:
            catalyst = (
                f"{SEL_TAG} high-conviction — {p['ticker']} {p['direction']}: CHOP regime, "
                f"class={p['lev']}, structure-confluence={p['conf']}, score={p['score']}, "
                f"quality={p['quality']}/3. Fired because the Phase-7-proven conditions stacked."
            )
            kw = dict(symbol=p["ticker"], direction=p["direction"], confidence=64.0,
                      catalyst=catalyst, category="selective", timeframe_days=1, prediction_tag="TA_ONLY")
            res = prediction_service.create_prediction(force=False, **kw)
            if not res.get("success"):
                res = prediction_service.create_prediction(force=True, **kw)
            if res.get("success"):
                posted.append(res["prediction_id"])
                logger.success(f"[Selective] posted #{res['prediction_id']} {p['ticker']} "
                               f"{p['direction']} quality={p['quality']}/3 conf={p['conf']}")
            else:
                logger.error(f"[Selective] FAILED {p['ticker']}: {res.get('error')}")

        logger.info(f"[Selective] fired {len(posted)} high-conviction ({', '.join(p['ticker'] for p in picks)})")
        return {"success": bool(posted), "posted": len(posted), "ids": posted}


selective_engine = SelectiveEngine()
