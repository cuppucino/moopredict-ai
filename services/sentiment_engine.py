import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from core.database import SessionLocal, NewsIntel, SocialPost, SentimentSnapshot, UserWatchlist
from services.ai_service import ai_service

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

    def score_text(self, text: str) -> float:
        """Fast VADER scoring (-1.0 to 1.0)."""
        if not text: return 0.0
        scores = self.analyzer.polarity_scores(text)
        return scores['compound']

    def score_symbol(self, symbol: str, lookback_hours: int = 48) -> Dict:
        """Aggregate sentiment from multiple sources with time decay."""
        db = SessionLocal()
        try:
            symbol = symbol.upper().strip()
            since = datetime.utcnow() - timedelta(hours=lookback_hours)
            
            # 1. Fetch Data
            news = db.query(NewsIntel).filter(NewsIntel.headline.ilike(f"%{symbol}%"), NewsIntel.scraped_at >= since).all()
            social = db.query(SocialPost).filter(SocialPost.content.ilike(f"%{symbol}%"), SocialPost.scraped_at >= since).all()
            
            if not news and not social:
                return {"score": 0.0, "label": "NEUTRAL", "reason": "No recent data found.", "count": 0}

            # 2. Process & Weight
            weighted_scores = []
            now = datetime.utcnow()
            
            # News: 1.5x weight
            for n in news:
                raw = self.score_text(n.headline)
                age_hours = (now - n.scraped_at).total_seconds() / 3600
                decay = math.exp(-0.02 * age_hours) # Decays over 48h
                weighted_scores.append(raw * 1.5 * decay)
            
            # Social: 0.8x weight
            for s in social:
                raw = self.score_text(s.content)
                age_hours = (now - s.scraped_at).total_seconds() / 3600
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
