import pytz
from datetime import datetime, time
from typing import Dict
from loguru import logger

class TradingWindowService:
    """
    Enforces time-based trading restrictions.
    Default: 60-minute morning window (13:30 - 14:30 UTC).
    """

    def __init__(self, start_utc: str = "13:30", end_utc: str = "14:30"):
        self.start_time = time.fromisoformat(start_utc)
        self.end_time = time.fromisoformat(end_utc)

    def is_in_window(self) -> bool:
        """Check if current time is within the allowed 60-minute morning window."""
        now_utc = datetime.now(pytz.utc).time()
        
        # Check if weekday
        if datetime.now(pytz.utc).weekday() >= 5: # Saturday=5, Sunday=6
            return False

        if self.start_time <= now_utc <= self.end_time:
            return True
        return False

    def validate_trade(self, is_paper: bool = True) -> Dict:
        """
        Validate if a trade is allowed based on the window.
        Rule: Paper trades are allowed during all market hours but with a warning 
        if outside the optimal 60-min morning window.
        """
        # Market Hours check (13:30 - 21:00 UTC)
        now_utc = datetime.now(pytz.utc).time()
        market_open = time(13, 30)
        market_close = time(21, 0)
        
        is_market_hours = market_open <= now_utc <= market_close
        
        if not is_market_hours:
            return {
                "allowed": False, 
                "reason": "Market is closed. Trades only allowed 13:30 - 21:00 UTC."
            }

        if self.is_in_window():
            return {"allowed": True, "reason": "Inside optimal morning 60-min window."}
        
        if is_paper:
            logger.warning("[TradingWindow] Paper trade allowed outside optimal 60-min window.")
            return {
                "allowed": True, 
                "warning": "Optimal morning 60-min window closed, but paper trading is permitted for learning.",
                "reason": "Outside optimal window"
            }
            
        return {
            "allowed": False, 
            "reason": f"Morning 60-min window closed. Optimal window: {self.start_time}-{self.end_time} UTC."
        }

trading_window = TradingWindowService()
