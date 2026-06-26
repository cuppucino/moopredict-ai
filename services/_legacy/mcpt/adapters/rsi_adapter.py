import pandas as pd
import numpy as np

def rsi_signal(ohlc: pd.DataFrame, period: int = 14, lower: int = 30, upper: int = 70) -> pd.Series:
    """
    RSI Strategy Adapter for MCPT.
    Returns:
      +1: RSI drops below `lower` (Buy)
      -1: RSI rises above `upper` (Sell)
      Holds previous state in between.
    """
    delta = ohlc['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    
    # Wilder's Smoothing
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    
    # Signal mapping
    signal = pd.Series(np.nan, index=ohlc.index)
    signal.loc[rsi < lower] = 1
    signal.loc[rsi > upper] = -1
    
    # Resample/align signals to the bar grid (forward-fill state, NaN->0 outside validity)
    signal = signal.ffill().fillna(0)
    
    return signal
