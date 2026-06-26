import pandas as pd
import numpy as np

def vwap_mean_reversion_signal(ohlc: pd.DataFrame, k: float = 2.0) -> pd.Series:
    """
    VWAP Mean Reversion Strategy Adapter for MCPT.
    Anchors VWAP to the start of each daily session.
    Computes running VWAP and Volume-Weighted Standard Deviation (VWSD).
    
    Mean Reversion logic:
      +1: Price drops below VWAP - k * VWSD (Oversold -> Buy)
      -1: Price rises above VWAP + k * VWSD (Overbought -> Sell)
      Holds previous state in between.
    """
    df = ohlc.copy()
    
    # Use index date as the session identifier
    df['date'] = df.index.date
    
    # Typical price
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
    df['pv'] = df['typical_price'] * df['volume']
    
    # For running variance: E[X^2]
    df['pv2'] = (df['typical_price']**2) * df['volume']
    
    # Group by session
    grouped = df.groupby('date')
    
    df['cum_vol'] = grouped['volume'].cumsum()
    df['cum_pv'] = grouped['pv'].cumsum()
    df['cum_pv2'] = grouped['pv2'].cumsum()
    
    # E[X] = VWAP
    cum_vol_safe = df['cum_vol'].replace(0, np.nan)
    df['vwap'] = df['cum_pv'] / cum_vol_safe
    
    # Var(X) = E[X^2] - (E[X])^2
    variance = (df['cum_pv2'] / cum_vol_safe) - (df['vwap']**2)
    # Clip to 0 to prevent floating point negative zeros
    variance = variance.clip(lower=0)
    df['vwap_std'] = np.sqrt(variance)
    
    # Bands
    upper_band = df['vwap'] + k * df['vwap_std']
    lower_band = df['vwap'] - k * df['vwap_std']
    
    # Signal mapping
    signal = pd.Series(np.nan, index=ohlc.index)
    signal.loc[df['close'] < lower_band] = 1
    signal.loc[df['close'] > upper_band] = -1
    
    # Forward-fill state mapping
    signal = signal.ffill().fillna(0)
    
    return signal
