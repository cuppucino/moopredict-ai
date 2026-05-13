from typing import Dict
from services.sentiment_engine import sentiment_engine

class SentimentService:
    def get_sentiment_score(self, symbol: str) -> Dict:
        """Compatibility wrapper for the new VADER-based SentimentEngine."""
        res = sentiment_engine.score_symbol(symbol)
        if "error" in res:
            return {"score": 0, "label": "NEUTRAL", "reason": f"Error: {res['error']}", "data_count": 0}
        
        # Add compatibility fields if missing
        if "reason" not in res:
            res["reason"] = f"Calculated via VADER across {res.get('data_count', 0)} data points."
            
        return res

sentiment_service = SentimentService()
