import pandas as pd
import numpy as np

def volume_profile_signal(ohlc: pd.DataFrame, shape_window: int = 48, vol_threshold: float = 1.0) -> pd.Series:
    """
    Volume Profile Strategy Adapter for MCPT.
    Classifies rolling Volume Profile shapes:
      - P-shape (Bullish / Top Heavy) -> +1
      - b-shape (Bearish / Bottom Heavy) -> -1
      - D-shape / other -> 0
    
    A signal is only active if total volume in the rolling window exceeds:
      vol_threshold * overall mean volume of the same window size.
    """
    try:
        n_bars = len(ohlc)
        signal = pd.Series(0, index=ohlc.index)
        
        if n_bars < shape_window:
            return signal
            
        highs = ohlc['high'].to_numpy()
        lows = ohlc['low'].to_numpy()
        vols = ohlc['volume'].to_numpy()
        
        # Calculate overall baseline to evaluate volume threshold
        rolling_vol = pd.Series(vols).rolling(shape_window).sum().fillna(0).to_numpy()
        mean_rolling_vol = np.mean(rolling_vol[shape_window-1:]) if len(rolling_vol) >= shape_window else 1.0
        
        signals_list = [0] * n_bars
        bins = 50
        
        for t in range(shape_window - 1, n_bars):
            # Check volume threshold first for speed
            current_vol = rolling_vol[t]
            if current_vol < vol_threshold * mean_rolling_vol:
                continue
                
            slice_highs = highs[t - shape_window + 1 : t + 1]
            slice_lows = lows[t - shape_window + 1 : t + 1]
            slice_vols = vols[t - shape_window + 1 : t + 1]
            
            p_min = np.min(slice_lows)
            p_max = np.max(slice_highs)
            
            if p_max == p_min:
                continue
                
            bin_edges = np.linspace(p_min, p_max, bins + 1)
            profile = np.zeros(bins)
            
            # Vectorize np.digitize across the entire slice
            start_idxs = np.digitize(slice_lows, bin_edges) - 1
            end_idxs = np.digitize(slice_highs, bin_edges) - 1
            
            # Fast vectorized/loop assignment for the profile
            for h, l, v, start_idx, end_idx in zip(slice_highs, slice_lows, slice_vols, start_idxs, end_idxs):
                if h == l:
                    if 0 <= start_idx < bins:
                        profile[start_idx] += v
                else:
                    num_bins_overlap = max(1, end_idx - start_idx + 1)
                    vol_per_bin = v / num_bins_overlap
                    for i in range(max(0, start_idx), min(bins, end_idx + 1)):
                        profile[i] += vol_per_bin
                        
            poc_idx = np.argmax(profile)
            one_third = bins // 3
            
            if poc_idx < one_third:
                signals_list[t] = -1  # b-shape (Bearish Rejection)
            elif poc_idx > 2 * one_third:
                signals_list[t] = 1   # P-shape (Bullish Breakout)
                
        signal = pd.Series(signals_list, index=ohlc.index)
    except Exception as e:
        # Robust fallback to neutral signal
        pass
        
    return signal

