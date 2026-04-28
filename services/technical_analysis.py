import pandas as pd
from typing import Dict, Optional, List
from loguru import logger
from futu import *
from services.moomoo_service import moomoo_service

class TechnicalAnalysisService:
    def _get_kline_data(self, symbol: str, num: int = 100) -> Optional[pd.DataFrame]:
        """Fetch K-line data from Moomoo."""
        if not moomoo_service.is_connected:
            if not moomoo_service.connect():
                return None

        # Ensure correct symbol format (e.g. US.AAPL)
        if "." not in symbol:
            # Heuristic: 5+ digits or starts with 0 usually HK, otherwise US
            if symbol.isdigit() and len(symbol) >= 4:
                symbol = f"HK.{symbol}"
            else:
                symbol = f"US.{symbol}"
        
        try:
            # 1. Subscribe to K-line data (required by Futu API)
            ret, data = moomoo_service.quote_ctx.subscribe([symbol], [SubType.K_DAY])
            if ret != RET_OK:
                logger.error(f"[TA] Subscription failed for {symbol}: {data}")
                return None
                
            # 2. Get current K-line
            ret, df = moomoo_service.quote_ctx.get_cur_kline(symbol, num, SubType.K_DAY, AuType.QFQ)
            if ret != RET_OK:
                logger.error(f"[TA] Failed to get K-lines for {symbol}: {df}")
                return None
                
            return df
        except Exception as e:
            logger.error(f"[TA] Exception fetching data for {symbol}: {e}")
            return None

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """Calculate Relative Strength Index."""
        try:
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return float(rsi.iloc[-1])
        except Exception as e:
            logger.error(f"[TA] RSI Error: {e}")
            return None

    def calculate_macd(self, df: pd.DataFrame) -> Dict:
        """Calculate MACD indicators."""
        try:
            ema12 = df['close'].ewm(span=12, adjust=False).mean()
            ema26 = df['close'].ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line
            
            return {
                "macd": float(macd_line.iloc[-1]),
                "signal": float(signal_line.iloc[-1]),
                "histogram": float(histogram.iloc[-1]),
                "trend": "BULLISH" if macd_line.iloc[-1] > signal_line.iloc[-1] else "BEARISH"
            }
        except Exception as e:
            logger.error(f"[TA] MACD Error: {e}")
            return {}

    def calculate_moving_averages(self, df: pd.DataFrame) -> Dict:
        """Calculate standard Moving Averages."""
        try:
            sma20 = df['close'].rolling(window=20).mean().iloc[-1]
            sma50 = df['close'].rolling(window=50).mean().iloc[-1]
            sma200 = df['close'].rolling(window=100).mean().iloc[-1] # Often 100 is enough if history is short
            current = df['close'].iloc[-1]
            
            return {
                "sma20": float(sma20),
                "sma50": float(sma50),
                "sma200": float(sma200),
                "position": "ABOVE" if current > sma50 else "BELOW"
            }
        except Exception as e:
            logger.error(f"[TA] MA Error: {e}")
            return {}

    def get_full_analysis(self, symbol: str) -> Dict:
        """Combine all technical indicators into one report."""
        df = self._get_kline_data(symbol)
        if df is None or df.empty:
            return {"error": f"Could not fetch data for {symbol}"}
            
        rsi = self.calculate_rsi(df)
        macd = self.calculate_macd(df)
        ma = self.calculate_moving_averages(df)
        
        current_price = float(df['close'].iloc[-1])
        
        # Summary logic
        summary = "NEUTRAL"
        if rsi and rsi < 30: summary = "OVERSOLD (BUY SIGNAL)"
        elif rsi and rsi > 70: summary = "OVERBOUGHT (SELL SIGNAL)"
        elif macd.get("trend") == "BULLISH" and current_price > ma.get("sma20", 0): summary = "BULLISH"
        elif macd.get("trend") == "BEARISH" and current_price < ma.get("sma20", 999999): summary = "BEARISH"

        return {
            "symbol": symbol,
            "price": current_price,
            "rsi": round(rsi, 2) if rsi else None,
            "macd": macd,
            "moving_averages": ma,
            "summary": summary,
            "timestamp": datetime.now().isoformat()
        }

ta_service = TechnicalAnalysisService()
