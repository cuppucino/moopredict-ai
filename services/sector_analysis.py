import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Disc",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication",
    "URA": "Uranium",
    "SPY": "S&P 500 (Market)",
    "QQQ": "Nasdaq (Tech)"
}

class SectorAnalysisService:
    def __init__(self):
        self._cache = {}
        self._cache_expiry = datetime.now()

    def get_sector_performance(self) -> List[Dict]:
        """Calculate performance for all major sectors with 1-hour cache."""
        if self._cache and datetime.now() < self._cache_expiry:
            return self._cache

        logger.info("[Sector] Calculating performance for all sectors...")
        results = []
        
        # We'll fetch 6 months of data to calculate 1D, 1W, 1M, 3M
        symbols = list(SECTOR_ETFS.keys())
        try:
            # Batch download for efficiency
            data = yf.download(symbols, period="6mo", interval="1d", progress=False)['Close']
            
            for symbol in symbols:
                if symbol not in data.columns:
                    continue
                
                prices = data[symbol].dropna()
                if len(prices) < 2:
                    continue
                
                current = prices.iloc[-1]
                prev_1d = prices.iloc[-2]
                prev_1w = prices.iloc[-6] if len(prices) >= 6 else prices.iloc[0]
                prev_1m = prices.iloc[-22] if len(prices) >= 22 else prices.iloc[0]
                
                perf = {
                    "symbol": symbol,
                    "name": SECTOR_ETFS[symbol],
                    "price": round(float(current), 2),
                    "change_1d": round(((current / prev_1d) - 1) * 100, 2),
                    "change_1w": round(((current / prev_1w) - 1) * 100, 2),
                    "change_1m": round(((current / prev_1m) - 1) * 100, 2)
                }
                results.append(perf)
            
            # Sort by 1-day performance descending
            results.sort(key=lambda x: x['change_1d'], reverse=True)
            
            # Update cache (1 hour)
            self._cache = results
            self._cache_expiry = datetime.now() + timedelta(hours=1)
            
            return results
            
        except Exception as e:
            logger.error(f"[Sector] Error during batch download: {e}")
            return []

    def get_sector_detail(self, symbol: str) -> Dict:
        """Get detailed performance for one sector."""
        symbol = symbol.upper()
        if symbol not in SECTOR_ETFS:
            return {"error": "Invalid sector ETF"}
            
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1mo")
            if hist.empty:
                return {"error": "No data found"}
                
            current = hist['Close'].iloc[-1]
            start = hist['Close'].iloc[0]
            
            return {
                "symbol": symbol,
                "name": SECTOR_ETFS[symbol],
                "price": round(float(current), 2),
                "month_change": round(((current / start) - 1) * 100, 2),
                "high_1m": round(float(hist['High'].max()), 2),
                "low_1m": round(float(hist['Low'].min()), 2)
            }
        except Exception as e:
            logger.error(f"[Sector] Error fetching {symbol}: {e}")
            return {"error": str(e)}

sector_service = SectorAnalysisService()
