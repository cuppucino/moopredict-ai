from typing import Dict
from services.ta_engine import ta_engine

class TechnicalAnalysisService:
    def get_full_analysis(self, symbol: str) -> Dict:
        """Drop-in replacement for the original TA service, now using the advanced TAEngine."""
        return ta_engine.get_full_analysis(symbol)
        
    def calculate_rsi(self, df, period: int = 14):
        """Legacy helper for backward compatibility."""
        # Using pandas_ta via engine
        import pandas_ta as ta
        rsi = df.ta.rsi(length=period)
        return float(rsi.iloc[-1]) if not rsi.empty else None

    def calculate_macd(self, df):
        """Legacy helper for backward compatibility."""
        import pandas_ta as ta
        macd = df.ta.macd()
        if macd.empty: return {}
        return {
            "macd": float(macd.iloc[-1, 0]),
            "signal": float(macd.iloc[-1, 2]),
            "histogram": float(macd.iloc[-1, 1]),
            "trend": "BULLISH" if macd.iloc[-1, 1] > 0 else "BEARISH"
        }

ta_service = TechnicalAnalysisService()
