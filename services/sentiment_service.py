import requests
from typing import Dict, List, Optional
from loguru import logger
from core.database import SessionLocal, NewsIntel, SocialPost
from services.ai_service import ai_service

from datetime import datetime, timedelta

# Simple in-memory cache for sentiment results
SENTIMENT_CACHE = {} # {symbol: {"data": result, "expiry": datetime}}

class SentimentService:
    def get_sentiment_score(self, symbol: str) -> Dict:
        """
        Analyze recent news and social media with caching.
        """
        symbol = symbol.upper().strip()
        
        # 1. Check Cache
        now = datetime.now()
        if symbol in SENTIMENT_CACHE:
            cached = SENTIMENT_CACHE[symbol]
            if now < cached["expiry"]:
                logger.debug(f"[Sentiment] Returning cached result for {symbol}")
                return cached["data"]

        db = SessionLocal()
        try:
            # 2. Fetch recent data (last 24-48 hours)
            # Reduce limit for high-volume stocks to avoid timeouts
            limit = 4 if symbol in ["AAPL", "NVDA", "AMD", "TSLA", "MSFT"] else 8
            news = db.query(NewsIntel).filter(NewsIntel.headline.ilike(f"%{symbol}%")).order_by(NewsIntel.scraped_at.desc()).limit(limit).all()
            social = db.query(SocialPost).filter(SocialPost.content.ilike(f"%{symbol}%")).order_by(SocialPost.scraped_at.desc()).limit(limit).all()
            
            if not news and not social:
                return {"score": 0, "label": "NEUTRAL", "reason": "Insufficient recent data for analysis.", "data_count": 0}
            
            content_to_analyze = []
            for n in news:
                content_to_analyze.append(f"[NEWS] {n.headline}")
            for s in social:
                content_to_analyze.append(f"[SOCIAL] {s.content[:120]}") # Shorter clips
            
            # 3. Use AI for sentiment scoring
            prompt = (
                f"Analyze sentiment for {symbol}.\n"
                f"Data:\n" + "\n".join(content_to_analyze[:10]) + "\n\n"
                f"Return JSON: {{'score': float, 'reason': 'string'}}"
            )
            
            result = ai_service.query_json(prompt)
            
            if "error" in result:
                return {"score": 0, "label": "NEUTRAL", "reason": f"AI Error: {result['error']}", "data_count": len(content_to_analyze)}

            score = float(result.get("score", 0))
            label = "BULLISH" if score >= 0.3 else "BEARISH" if score <= -0.3 else "NEUTRAL"
            
            final_result = {
                "score": score,
                "label": label,
                "reason": result.get("reason", "No reason provided."),
                "data_count": len(content_to_analyze),
                "timestamp": now.isoformat()
            }
            
            # 4. Save to Cache (Expire in 30 minutes)
            SENTIMENT_CACHE[symbol] = {
                "data": final_result,
                "expiry": now + timedelta(minutes=30)
            }
            
            return final_result
            
        except Exception as e:
            logger.error(f"[Sentiment] Error for {symbol}: {e}")
            return {"score": 0, "label": "ERROR", "reason": str(e), "data_count": 0}
        finally:
            db.close()

sentiment_service = SentimentService()
