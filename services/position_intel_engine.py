"""
Position-Intel Engine — daily INTELLIGENCE gathering on the user's held positions only.

Complements the position-watch:
  - position-watch  → PREDICTS direction on each holding
  - position-intel  → GATHERS the "what's happening & why" — earnings dates, sentiment,
                      and ticker-matched news for each holding

Two jobs (kf, 2026-07-24):
  1. A daily intel briefing FOR the user — per holding: upcoming earnings (event risk),
     sentiment, matched headlines.
  2. FEED richer per-symbol signal into the position-watch so its predictions stop
     defaulting to regime-UP when the broad news scan is thin on single stocks.

Deterministic gathering (reuses earnings_service, sentiment_engine, the news store, and the
ticker-alias map). No LLM in the path — the small local model is only used elsewhere to
summarize. Scoped strictly to what the user holds.
"""
from typing import Dict, List
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

from services.moomoo_service import moomoo_service
from services.earnings_calendar import earnings_service
from services.sentiment_engine import sentiment_engine
from services.political_monitor import TICKER_ALIASES
from core.database import SessionLocal, NewsIntel

INTEL_DIR = Path(__file__).resolve().parent.parent / "data" / "position_intel"


def _bare(symbol: str) -> str:
    return (symbol or "").split(".")[-1].strip().upper()


class PositionIntelEngine:
    def gather(self, symbols: List[str]) -> Dict:
        """Return {symbol: {...intel...}} for the given symbols. Reused by the position-watch."""
        cutoff = datetime.utcnow() - timedelta(hours=48)
        db = SessionLocal()
        try:
            news = db.query(NewsIntel).filter(NewsIntel.scraped_at >= cutoff).all()
        finally:
            db.close()

        intel = {}
        for sym in symbols:
            # --- earnings (biggest catalyst / event-risk flag for single stocks) ---
            edays, edate = None, None
            try:
                e = earnings_service.get_stock_earnings(sym) or {}
                dates = e.get("earnings_dates") or []
                if dates:
                    edate = str(dates[0])[:10]
                    try:
                        edays = (datetime.strptime(edate, "%Y-%m-%d") - datetime.utcnow()).days
                    except Exception:
                        pass
            except Exception as ex:
                logger.debug(f"[PositionIntel] earnings {sym}: {ex}")

            # --- sentiment ---
            try:
                s = sentiment_engine.score_symbol(sym) or {}
                sscore = float(s.get("score", 0.0) or 0.0)
                slabel = s.get("label", "NEUTRAL")
                # data_count on populated path (audit: "count" was always 0 — briefings
                # showed "BULLISH from 0 mentions").
                scount = int(s.get("data_count", s.get("count", 0)) or 0)
            except Exception:
                sscore, slabel, scount = 0.0, "NEUTRAL", 0

            # --- news matched by ticker + its aliases (word-boundary; audit: substring
            # matching served "dramatic" headlines as DRAM intel) ---
            from services.political_monitor import matches_ticker
            heads = []
            for n in news:
                if matches_ticker(f"{n.headline or ''} {n.summary or ''}", sym):
                    heads.append(n.headline)

            intel[sym] = {
                "earnings_date": edate, "earnings_days": edays,
                "sentiment_score": round(sscore, 3), "sentiment_label": slabel,
                "sentiment_count": scount,
                "news_count": len(heads), "headlines": heads[:3],
            }
        return intel

    def generate_briefing(self) -> Dict:
        """Gather intel on currently-held positions, write a markdown briefing, return it."""
        try:
            positions = moomoo_service.get_positions() or []
        except Exception as e:
            logger.error(f"[PositionIntel] get_positions failed: {e}")
            return {"success": False, "error": str(e)}
        symbols = sorted({_bare(p.get("symbol", "")) for p in positions if p.get("symbol")})
        symbols = [s for s in symbols if s]
        if not symbols:
            return {"success": True, "symbols": [], "note": "no positions"}

        intel = self.gather(symbols)
        try:
            INTEL_DIR.mkdir(parents=True, exist_ok=True)
            (INTEL_DIR / f"{datetime.utcnow().date().isoformat()}.md").write_text(
                self._render(symbols, intel))
        except Exception as e:
            logger.error(f"[PositionIntel] write failed: {e}")
        logger.info(f"[PositionIntel] briefing for {', '.join(symbols)}")
        return {"success": True, "symbols": symbols, "intel": intel}

    def _render(self, symbols: List[str], intel: Dict) -> str:
        out = [f"# Position Intel — {datetime.utcnow().date().isoformat()}",
               "> Intelligence on your held positions only. Informational, not advice.", ""]
        for s in symbols:
            i = intel[s]
            out.append(f"## {s}")
            if i["earnings_days"] is not None:
                flag = "  ⚠️ EVENT RISK — earnings this week" if 0 <= (i["earnings_days"] or 99) <= 7 else ""
                out.append(f"- **Earnings:** {i['earnings_date']} ({i['earnings_days']}d away){flag}")
            out.append(f"- **Sentiment:** {i['sentiment_label']} ({i['sentiment_score']:+.2f}) "
                       f"from {i['sentiment_count']} mentions")
            out.append(f"- **News (48h):** {i['news_count']} headline(s) matched")
            for h in i["headlines"]:
                out.append(f"    - {h}")
            out.append("")
        return "\n".join(out)


position_intel_engine = PositionIntelEngine()
