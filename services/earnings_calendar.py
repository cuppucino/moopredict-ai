import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger

class EarningsCalendarService:
    def get_stock_earnings(self, symbol: str) -> Dict:
        """Fetch upcoming earnings date for a specific stock."""
        # Clean symbol for yfinance (e.g. US.AAPL -> AAPL)
        yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
        
        try:
            ticker = yf.Ticker(yf_symbol)
            calendar = ticker.calendar
            
            if calendar is not None:
                # Handle both older DataFrame and newer dict types from yfinance
                is_empty = False
                if hasattr(calendar, "empty"):
                    is_empty = calendar.empty
                elif isinstance(calendar, dict):
                    is_empty = len(calendar) == 0
                
                if not is_empty:
                    # Try to extract from dict or DF
                    if isinstance(calendar, dict):
                        dates = calendar.get("Earnings Date", [])
                        eps = calendar.get("EPS Estimate")
                        rev = calendar.get("Revenue Estimate")
                    else:
                        dates = calendar.get("Earnings Date", [])
                        eps = calendar.get("EPS Estimate", [None])[0]
                        rev = calendar.get("Revenue Estimate", [None])[0]
                    
                    if hasattr(dates, "tolist"):
                        dates = dates.tolist()
                    
                    formatted_dates = [d.strftime("%Y-%m-%d") for d in dates if hasattr(d, "strftime")]
                    
                    return {
                        "symbol": symbol,
                        "earnings_dates": formatted_dates,
                        "eps_estimate": eps,
                        "revenue_estimate": rev,
                        "success": True
                    }
            
            # Fallback for some stocks where .calendar is empty but .info might have it
            info = ticker.info
            if "nextEarningsDate" in info:
                dt = datetime.fromtimestamp(info["nextEarningsDate"])
                return {
                    "symbol": symbol,
                    "earnings_dates": [dt.strftime("%Y-%m-%d")],
                    "success": True
                }

            return {"symbol": symbol, "success": False, "error": "No calendar data found"}
            
        except Exception as e:
            logger.error(f"[Earnings] Error fetching for {symbol}: {e}")
            return {"symbol": symbol, "success": False, "error": str(e)}

    def get_watchlist_earnings(self, symbols: List[str]) -> List[Dict]:
        """Fetch earnings for a list of symbols."""
        results = []
        for sym in symbols:
            results.append(self.get_stock_earnings(sym))
        return results

earnings_service = EarningsCalendarService()
