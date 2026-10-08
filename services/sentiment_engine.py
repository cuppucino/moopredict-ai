import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from loguru import logger
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from core.database import SessionLocal, NewsIntel, SocialPost, SentimentSnapshot, UserWatchlist
from services.ai_service import ai_service
from services.evidence_quality import news_record, summarize_evidence

class SentimentEngine:
    def __init__(self):
        self.analyzer = SentimentIntensityAnalyzer()
        # Custom weights for financial terms (VADER isn't perfect for finance)
        self.analyzer.lexicon.update({
            'bullish': 2.0, 'bearish': -2.0, 'moon': 2.0, 'dump': -2.0,
            'call': 0.5, 'put': -0.5, 'breakout': 1.5, 'crash': -2.5,
            'buy': 1.0, 'sell': -1.0, 'hold': 0.0, 'upgrade': 2.0,
            'downgrade': -2.0, 'ath': 1.5, 'bottom': 1.0, 'top': -1.0
        })

    @staticmethod
    def _naive_utc(dt: datetime) -> datetime:
        """Mixed rows: older scrapes stored naive-UTC, newer ones tz-aware. Normalize
        to naive UTC so age math never throws (the SPY every-30-min crash)."""
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    def score_text(self, text: str) -> float:
        """Fast VADER scoring (-1.0 to 1.0)."""
        if not text: return 0.0
        scores = self.analyzer.polarity_scores(text)
        return scores['compound']

    def score_symbol(self, symbol: str, lookback_hours: int = 48, trace=None) -> Dict:
        """Aggregate sentiment from multiple sources with time decay."""
        db = SessionLocal()
        try:
            symbol = symbol.upper().strip()
            # news_intel.scraped_at is timestamptz — use an AWARE cutoff so the window
            # is exactly lookback_hours (a naive cutoff was interpreted in session TZ
            # and silently widened 48h to 56h). social_posts is naive-UTC: naive cutoff.
            since_aware = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
            since = datetime.utcnow() - timedelta(hours=lookback_hours)

            # 1. Fetch recent rows, then match in Python via word-boundary ticker/alias
            # matching (audit 2026-08-13: ILIKE '%SPY%' matched 0/519 headlines — news
            # writes "Nvidia" and "S&P 500", not "NVDA"/"SPY"; and '%SMH%' matched HTML
            # junk). matches_ticker covers $TICKER, \bTICKER\b, and company aliases.
            from services.political_monitor import matches_ticker
            recent_news = db.query(NewsIntel).filter(NewsIntel.scraped_at >= since_aware).all()
            news = [n for n in recent_news
                    if matches_ticker(f"{n.headline or ''} {n.summary or ''}", symbol)]
            recent_social = db.query(SocialPost).filter(SocialPost.scraped_at >= since).all()
            social = [p for p in recent_social if matches_ticker(p.content or "", symbol)]
            observed_at = datetime.now(timezone.utc)
            news_inputs = [news_record(n) for n in news]
            social_inputs = [{"id": p.id, "content": p.content,
                              "scraped_at": p.scraped_at.isoformat() if p.scraped_at else None,
                              "published_at": None} for p in social]
            quality = summarize_evidence(news_inputs, social_inputs,
                                         observed_at=observed_at, lookback_hours=lookback_hours)
            if trace is not None:
                trace["sentiment_inputs"] = {
                    "observed_at": observed_at.isoformat(), "news": news_inputs,
                    "social": social_inputs}
            
            if not news and not social:
                return {"symbol": symbol, "score": 0.0, "label": "NO_DATA",
                        "reason": "No matching evidence; zero is a model fallback, not neutral evidence.",
                        "count": 0, "data_count": 0, "news_count": 0, "social_count": 0,
                        "evidence_quality": quality}

            # 2. Process & Weight
            weighted_scores = []
            now = datetime.utcnow()
            
            # News: 1.5x weight
            for n in news:
                raw = self.score_text(n.headline)
                age_hours = (now - self._naive_utc(n.scraped_at)).total_seconds() / 3600
                decay = math.exp(-0.02 * age_hours) # Decays over 48h
                weighted_scores.append(raw * 1.5 * decay)
            
            # Social: 0.8x weight
            for s in social:
                raw = self.score_text(s.content)
                age_hours = (now - self._naive_utc(s.scraped_at)).total_seconds() / 3600
                decay = math.exp(-0.04 * age_hours) # Social decays faster
                weighted_scores.append(raw * 0.8 * decay)
                
            final_score = sum(weighted_scores) / len(weighted_scores) if weighted_scores else 0.0
            # Clamp to [-1, 1]
            final_score = max(-1.0, min(1.0, final_score))
            
            label = "BULLISH" if final_score >= 0.2 else "BEARISH" if final_score <= -0.2 else "NEUTRAL"
            
            return {
                "symbol": symbol,
                "score": round(final_score, 3),
                "label": label,
                "data_count": len(news) + len(social),
                "news_count": len(news),
                "social_count": len(social),
                "evidence_quality": quality,
                "timestamp": now.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[SentimentEngine] Error for {symbol}: {e}")
            return {"error": str(e)}
        finally:
            db.close()

    def detect_shift(self, symbol: str) -> Optional[str]:
        """Detect a significant sentiment swing in the last 24h vs 48h."""
        db = SessionLocal()
        try:
            # Simple check against previous snapshot
            last_two = db.query(SentimentSnapshot).filter(SentimentSnapshot.symbol == symbol).order_by(SentimentSnapshot.timestamp.desc()).limit(2).all()
            if len(last_two) < 2: return None
            
            current = last_two[0].score
            previous = last_two[1].score
            diff = current - previous
            
            if abs(diff) >= 0.4:
                direction = "IMPROVING" if diff > 0 else "DETERIORATING"
                return f"Sentiment is {direction} rapidly ({previous:.2f} -> {current:.2f})"
            return None
        finally:
            db.close()

    def save_snapshot(self, symbol: str):
        """Save a sentiment snapshot to history."""
        data = self.score_symbol(symbol)
        if "error" in data or data.get("data_count", 0) == 0:
            return

        db = SessionLocal()
        try:
            snapshot = SentimentSnapshot(
                symbol=symbol,
                score=data["score"],
                label=data["label"],
                data_count=data["data_count"],
                raw_json=data
            )
            db.add(snapshot)
            db.commit()
            logger.info(f"[SentimentEngine] Snapshot saved for {symbol}: {data['score']}")
        except Exception as e:
            logger.error(f"[SentimentEngine] DB Error: {e}")
            db.rollback()
        finally:
            db.close()

sentiment_engine = SentimentEngine()
