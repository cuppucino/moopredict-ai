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
    "IGV": "Software",
    "SMH": "Semiconductors",
    "IBIT": "Crypto/Bitcoin",
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

    def calculate_rrg(self, benchmark: str = "SPY") -> List[Dict]:
        """
        Calculate Relative Rotation Graph (RRG) classification for all sectors.
        Classifies sectors into Leading, Weakening, Lagging, Improving.
        """
        symbols = list(SECTOR_ETFS.keys())
        if benchmark not in symbols: symbols.append(benchmark)
        
        try:
            data = yf.download(symbols, period="1y", interval="1d", progress=False)['Close']
            if benchmark not in data.columns: return []
            
            spy = data[benchmark].dropna()
            results = []
            
            for symbol in SECTOR_ETFS.keys():
                if symbol == benchmark or symbol not in data.columns: continue
                
                prices = data[symbol].dropna()
                if len(prices) < 30: continue
                
                # RS-Ratio: normalized(sector / spy)
                ratio = prices / spy
                rs_ratio = 100 * (ratio / ratio.rolling(window=14).mean())
                
                # RS-Momentum: rate of change of RS-Ratio
                rs_momentum = 100 * (rs_ratio / rs_ratio.rolling(window=14).mean())
                
                curr_ratio = float(rs_ratio.iloc[-1])
                curr_mom = float(rs_momentum.iloc[-1])
                
                # Classify Quadrant
                if curr_ratio >= 100 and curr_mom >= 100: quadrant = "LEADING"
                elif curr_ratio >= 100 and curr_mom < 100: quadrant = "WEAKENING"
                elif curr_ratio < 100 and curr_mom < 100: quadrant = "LAGGING"
                else: quadrant = "IMPROVING"
                
                results.append({
                    "symbol": symbol,
                    "name": SECTOR_ETFS[symbol],
                    "rs_ratio": round(curr_ratio, 2),
                    "rs_momentum": round(curr_mom, 2),
                    "quadrant": quadrant
                })
                
            return results
        except Exception as e:
            logger.error(f"[Sector] RRG Error: {e}")
            return []

    def get_sector_for_stock(self, symbol: str) -> str:
        """Map a stock symbol to its respective sector ETF."""
        # Hardcoded common mappings for efficiency
        MAPPING = {
            "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "TSLA": "XLY", "AMZN": "XLY",
            "GOOG": "XLC", "META": "XLC", "NFLX": "XLC", "JPM": "XLF", "V": "XLF",
            "XOM": "XLE", "CVX": "XLE", "JNJ": "XLV", "PFE": "XLV", "CAT": "XLI",
            "HD": "XLY", "PG": "XLP", "COST": "XLP", "NEE": "XLU", "PLTR": "XLK",
            "UEC": "URA", "CCJ": "URA", "AMD": "XLK", "SMCI": "SMH"
        }
        
        sym = symbol.upper()
        if sym in MAPPING: return MAPPING[sym]
        
        # Fallback to yfinance sector info
        try:
            ticker = yf.Ticker(sym)
            sector_name = ticker.info.get('sector', '')
            # Map sector name to ETF
            NAME_TO_ETF = {
                "Technology": "XLK", "Financial Services": "XLF", "Energy": "XLE",
                "Healthcare": "XLV", "Industrials": "XLI", "Consumer Cyclical": "XLY",
                "Consumer Defensive": "XLP", "Utilities": "XLU", "Basic Materials": "XLB",
                "Real Estate": "XLRE", "Communication Services": "XLC"
            }
            return NAME_TO_ETF.get(sector_name, "SPY")
        except:
            return "SPY"

    def get_rrg_influence(self, symbol: str) -> int:
        """Calculate score influence based on stock's sector rotation quadrant."""
        sector_etf = self.get_sector_for_stock(symbol)
        if sector_etf == "SPY": return 0
        
        rrg_data = self.calculate_rrg()
        sector_data = next((s for s in rrg_data if s['symbol'] == sector_etf), None)
        
        if not sector_data: return 0
        
        quadrant = sector_data['quadrant']
        # Weights: Leading (+15), Improving (+5), Weakening (-5), Lagging (-15)
        SCORES = {
            "LEADING": 15,
            "IMPROVING": 5,
            "WEAKENING": -5,
            "LAGGING": -15
        }
        return SCORES.get(quadrant, 0)

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
