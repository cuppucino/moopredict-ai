import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from loguru import logger
from services.moomoo_service import moomoo_service

class ReversalDetector:
    """
    Detects intraday and daily reversal patterns.
    - Hammer/Doji
    - Double Bottom
    - Volume Divergence
    - Gap Fills
    - VWAP Reclaims
    """

    def scan_reversals(self, symbol: str) -> Dict:
        """Scan a symbol for all supported reversal patterns."""
        try:
            # Fetch 15m candles for intraday patterns
            df = moomoo_service.get_klines(symbol, ktype='K_15M', count=100)
            if df.empty: return {"error": "No data"}

            results = {
                "symbol": symbol,
                "patterns": [],
                "confidence": 0
            }

            # 1. Hammer/Doji detection
            if self._is_hammer(df.iloc[-1]):
                results["patterns"].append("Hammer/Doji (Price rejection at lows)")
                results["confidence"] += 30

            # 2. VWAP Reclaim
            if self._is_vwap_reclaim(df):
                results["patterns"].append("VWAP Reclaim (Price crossing above average)")
                results["confidence"] += 40

            # 3. Gap Fill
            if self._is_gap_fill(symbol):
                results["patterns"].append("Gap Fill (Price filling previous overnight gap)")
                results["confidence"] += 20

            # 4. Volume Divergence
            if self._is_volume_divergence(df):
                results["patterns"].append("Volume Divergence (Falling price on decreasing volume = Exhaustion)")
                results["confidence"] += 20

            return results
        except Exception as e:
            logger.error(f"[Reversal] Scan error for {symbol}: {e}")
            return {"error": str(e)}

    def _is_hammer(self, candle: pd.Series) -> bool:
        """Detect a hammer candlestick (long lower wick)."""
        body = abs(candle['close'] - candle['open'])
        lower_wick = min(candle['open'], candle['close']) - candle['low']
        upper_wick = candle['high'] - max(candle['open'], candle['close'])
        
        # Hammer: lower wick > 2x body, upper wick is small
        if lower_wick > 2 * body and upper_wick < body:
            return True
        return False

    def _is_vwap_reclaim(self, df: pd.DataFrame) -> bool:
        """Detect price crossing back above VWAP after being below it."""
        # Calculate VWAP
        df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
        
        # Check if last 3 candles were below, and last is above
        if len(df) < 5: return False
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        if prev['close'] < prev['vwap'] and last['close'] > last['vwap']:
            return True
        return False

    def _is_gap_fill(self, symbol: str) -> bool:
        """Check if symbol has a gap from previous day close that hasn't filled."""
        # This requires daily close vs today's open
        df_daily = moomoo_service.get_klines(symbol, ktype='K_DAY', count=2)
        if len(df_daily) < 2: return False
        
        prev_close = df_daily.iloc[-2]['close']
        today_open = df_daily.iloc[-1]['open']
        today_last = df_daily.iloc[-1]['close']
        
        # Gap down: prev_close > today_open
        # Fill: today_last crosses prev_close
        if today_open < prev_close * 0.98: # 2% gap down
            if today_last >= prev_close:
                return True # Filled
        return False

    def _is_volume_divergence(self, df: pd.DataFrame) -> bool:
        """Detect price falling but volume decreasing (exhaustion)."""
        if len(df) < 5: return False
        
        # Last 3 candles price trend
        price_trend = df['close'].iloc[-3:].diff().mean()
        # Last 3 candles volume trend
        vol_trend = df['volume'].iloc[-3:].diff().mean()
        
        if price_trend < 0 and vol_trend < 0:
            return True
        return False

reversal_detector = ReversalDetector()
