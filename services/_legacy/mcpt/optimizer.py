import pandas as pd
import numpy as np
from typing import Tuple, Dict

from services._legacy.mcpt.profit_factor import compute_pf
from services._legacy.mcpt.adapters.rsi_adapter import rsi_signal
from services._legacy.mcpt.adapters.vwap_adapter import vwap_mean_reversion_signal
from services._legacy.mcpt.adapters.reversal_adapter import reversal_signal
from services._legacy.mcpt.adapters.volume_profile_adapter import volume_profile_signal

def optimize_rsi(ohlc: pd.DataFrame, min_trades: int = 50) -> Tuple[Dict, float]:
    """
    Grid search RSI with coarsened grid to prevent selection bias:
    period in {7, 10, 14, 21}
    lower in {25, 30, 35, 40}
    upper in {60, 65, 70, 75}
    Total 64 combinations.
    Returns (best_params, best_pf)
    """
    periods = [7, 10, 14, 21]
    lowers = [25, 30, 35, 40]
    uppers = [60, 65, 70, 75]
    
    best_pf = -1.0
    best_params = {'period': periods[0], 'lower': lowers[0], 'upper': uppers[0]}
    
    try:
        if 'r' in ohlc.columns:
            r = ohlc['r']
        else:
            r = np.log(ohlc['close']).diff().shift(-1)
            
        close = ohlc['close'].to_numpy()
        delta = np.diff(close, prepend=close[0])
        gain = np.clip(delta, 0, None)
        loss = -np.clip(delta, None, 0)
        
        # Precompute RSI arrays for the 4 periods
        rsi_series_dict = {}
        for p in periods:
            gain_series = pd.Series(gain)
            loss_series = pd.Series(loss)
            avg_gain = gain_series.ewm(com=p - 1, adjust=False).mean().to_numpy()
            avg_loss = loss_series.ewm(com=p - 1, adjust=False).mean().to_numpy()
            rs = avg_gain / (avg_loss + 1e-9)
            rsi_series_dict[p] = 100 - (100 / (1 + rs))
            
        for p in periods:
            rsi_arr = rsi_series_dict[p]
            for l in lowers:
                for u in uppers:
                    signal_arr = np.full(len(ohlc), np.nan)
                    signal_arr[rsi_arr < l] = 1.0
                    signal_arr[rsi_arr > u] = -1.0
                    
                    signal = pd.Series(signal_arr, index=ohlc.index).ffill().fillna(0.0)
                    n_trades = int((signal.diff().fillna(0) != 0).sum())
                    
                    if n_trades < min_trades:
                        continue
                        
                    pf = compute_pf(signal, r)
                    
                    if pf > best_pf and pf != float('inf'):
                        best_pf = pf
                        best_params = {'period': p, 'lower': l, 'upper': u}
    except Exception as e:
        # Fail-safe returning initial/default params and negative profit factor
        pass
        
    return best_params, best_pf

def optimize_vwap(ohlc: pd.DataFrame, min_trades: int = 50) -> Tuple[Dict, float]:
    """
    Grid search VWAP mean reversion with parameter k:
    k in {1.0, 1.5, 2.0, 2.5, 3.0}
    """
    k_values = [1.0, 1.5, 2.0, 2.5, 3.0]
    
    best_pf = -1.0
    best_params = {'k': k_values[0]}
    
    try:
        if 'r' in ohlc.columns:
            r = ohlc['r']
        else:
            r = np.log(ohlc['close']).diff().shift(-1)
            
        for k in k_values:
            signal = vwap_mean_reversion_signal(ohlc, k=k)
            n_trades = int((signal.diff().fillna(0) != 0).sum())
            if n_trades < min_trades:
                continue
                
            pf = compute_pf(signal, r)
            
            if pf > best_pf and pf != float('inf'):
                best_pf = pf
                best_params = {'k': k}
    except Exception as e:
        pass
        
    return best_params, best_pf

def optimize_reversal(ohlc: pd.DataFrame, min_trades: int = 50) -> Tuple[Dict, float]:
    """
    Grid search Reversal with parameter conf_threshold:
    threshold in {40, 50, 70, 90}
    """
    thresholds = [40, 50, 70, 90]
    
    best_pf = -1.0
    best_params = {'conf_threshold': thresholds[0]}
    
    try:
        if 'r' in ohlc.columns:
            r = ohlc['r']
        else:
            r = np.log(ohlc['close']).diff().shift(-1)
            
        for th in thresholds:
            signal = reversal_signal(ohlc, conf_threshold=th)
            n_trades = int((signal.diff().fillna(0) != 0).sum())
            if n_trades < min_trades:
                continue
                
            pf = compute_pf(signal, r)
            
            if pf > best_pf and pf != float('inf'):
                best_pf = pf
                best_params = {'conf_threshold': th}
    except Exception as e:
        pass
        
    return best_params, best_pf

def optimize_volume_profile(ohlc: pd.DataFrame, min_trades: int = 50) -> Tuple[Dict, float]:
    """
    Grid search Volume Profile classification with parameters:
    shape_window in {24, 48, 72}
    vol_threshold in {0.5, 1.0, 1.5}
    """
    windows = [24, 48, 72]
    thresholds = [0.5, 1.0, 1.5]
    
    best_pf = -1.0
    best_params = {'shape_window': windows[0], 'vol_threshold': thresholds[0]}
    
    try:
        if 'r' in ohlc.columns:
            r = ohlc['r']
        else:
            r = np.log(ohlc['close']).diff().shift(-1)
            
        for w in windows:
            for t in thresholds:
                signal = volume_profile_signal(ohlc, shape_window=w, vol_threshold=t)
                n_trades = int((signal.diff().fillna(0) != 0).sum())
                if n_trades < min_trades:
                    continue
                    
                pf = compute_pf(signal, r)
                
                if pf > best_pf and pf != float('inf'):
                    best_pf = pf
                    best_params = {'shape_window': w, 'vol_threshold': t}
    except Exception as e:
        pass
        
    return best_params, best_pf
