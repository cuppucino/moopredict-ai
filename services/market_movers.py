import os
import asyncio
from typing import List, Dict, Optional
from loguru import logger
from futu import *
from services.moomoo_service import moomoo_service

class MarketMoversService:
    def __init__(self):
        self.symbols = [
            # Tech Giants
            "US.AAPL", "US.MSFT", "US.GOOGL", "US.AMZN", "US.NVDA", "US.META", "US.TSLA", "US.AMD",
            # Financials
            "US.JPM", "US.V", "US.MA", "US.BAC", "US.GS",
            # Healthcare
            "US.UNH", "US.JNJ", "US.LLY", "US.PFE",
            # Energy
            "US.XOM", "US.CVX", "US.COP", "US.SLB",
            # High Interest
            "US.SMCI", "US.PLTR", "US.MSTR", "US.COIN", "US.MARA", "US.RIOT",
            # ETFs
            "US.SPY", "US.QQQ", "US.IWM", "US.XLE", "US.URA"
        ]
        self._cache = {}
        self._last_update = 0
        self._cache_duration = 60  # 1 minute cache

    async def get_movers(self, limit: int = 10) -> Dict:
        """Fetch market movers from the tracked stock list."""
        try:
            # Check cache
            import time
            if time.time() - self._last_update < self._cache_duration and self._cache:
                return self._cache

            if not moomoo_service.quote_ctx:
                logger.error("Moomoo quote context not initialized")
                return {"gainers": [], "losers": [], "most_active": []}

            # Fetch snapshots for all symbols
            ret, data = moomoo_service.quote_ctx.get_market_snapshot(self.symbols)
            if ret != RET_OK:
                logger.error(f"Failed to fetch market snapshots: {data}")
                return {"gainers": [], "losers": [], "most_active": []}

            stocks = []
            for _, row in data.iterrows():
                # Extract clean symbol
                symbol = row['code'].replace("US.", "")
                
                # Calculate change
                # price, last_price, prev_close_price are common fields
                price = float(row['last_price'])
                prev_close = float(row['prev_close_price'])
                change = ((price - prev_close) / prev_close * 100) if prev_close else 0
                
                stocks.append({
                    "symbol": symbol,
                    "name": row.get('name', symbol),
                    "price": round(price, 2),
                    "change": round(change, 2),
                    "volume": int(row['volume'])
                })

            # Sort for movers
            gainers = sorted(stocks, key=lambda x: x['change'], reverse=True)
            losers = sorted(stocks, key=lambda x: x['change'])
            most_active = sorted(stocks, key=lambda x: x['volume'], reverse=True)

            result = {
                "gainers": gainers[:limit],
                "losers": losers[:limit],
                "most_active": most_active[:limit]
            }

            self._cache = result
            self._last_update = time.time()
            return result

        except Exception as e:
            logger.error(f"Error in MarketMoversService: {e}")
            return {"gainers": [], "losers": [], "most_active": []}

market_movers_service = MarketMoversService()
