import pandas as pd
import numpy as np

def reversal_signal(ohlc: pd.DataFrame, conf_threshold: int = 50) -> pd.Series:
    """
    Vectorized adapter for ReversalDetector.
    Evaluates:
      - Hammer/Doji (30 conf)
      - VWAP Reclaim (40 conf)
      - Volume Divergence (20 conf)
      
    Returns +1 when cumulative confidence >= conf_threshold, else 0.
    """
    df = ohlc.copy()
    confidence = pd.Series(0, index=df.index)
    
    # 1. Hammer
    body = abs(df['close'] - df['open'])
    lower_wick = df[['open', 'close']].min(axis=1) - df['low']
    upper_wick = df['high'] - df[['open', 'close']].max(axis=1)
    
    is_hammer = (lower_wick > 2 * body) & (upper_wick < body)
    confidence += is_hammer.astype(int) * 30
    
    # 2. VWAP Reclaim (Session Anchored)
    df['date'] = df.index.date
    # Vectorized session vwap
    df['pv'] = df['close'] * df['volume']
    grouped = df.groupby('date')
    df['vwap'] = grouped['pv'].cumsum() / grouped['volume'].cumsum().replace(0, np.nan)
    
    prev_close = df['close'].shift(1)
    prev_vwap = df['vwap'].shift(1)
    is_vwap_reclaim = (prev_close < prev_vwap) & (df['close'] > df['vwap'])
    confidence += is_vwap_reclaim.astype(int) * 40
    
    # 3. Volume Divergence
    price_diff = df['close'].diff()
    vol_diff = df['volume'].diff()
    
    price_trend = price_diff.rolling(3).mean()
    vol_trend = vol_diff.rolling(3).mean()
    
    is_vol_div = (price_trend < 0) & (vol_trend < 0)
    confidence += is_vol_div.astype(int) * 20
    
    # Generate Signal (Hold for 1 bar)
    signal = pd.Series(0, index=df.index)
    signal.loc[confidence >= conf_threshold] = 1
    
    return signal
