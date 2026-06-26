import pandas as pd
import numpy as np
from loguru import logger

from services._legacy.mcpt.adapters.rsi_adapter import rsi_signal
from services._legacy.mcpt.adapters.vwap_adapter import vwap_mean_reversion_signal
from services._legacy.mcpt.adapters.reversal_adapter import reversal_signal
from services._legacy.mcpt.adapters.volume_profile_adapter import volume_profile_signal

# Optimal parameters found during standalone optimizations
TICKER_BASE_PARAMS = {
    'MARA': {
        'vwap': {'k': 1.5},
        'reversal': {'conf_threshold': 50},
        'volume_profile': {'shape_window': 48, 'vol_threshold': 0.5}
    },
    'CVX': {
        'rsi': {'period': 7, 'lower': 35, 'upper': 65},
        'vwap': {'k': 2.0},
        'reversal': {'conf_threshold': 40},
        'volume_profile': {'shape_window': 24, 'vol_threshold': 0.5}
    },
    'SPY': {
        'vwap': {'k': 1.5},
        'volume_profile': {'shape_window': 48, 'vol_threshold': 1.0}
    },
    'QQQ': {
        'vwap': {'k': 1.0},
        'volume_profile': {'shape_window': 24, 'vol_threshold': 0.5}
    },
    'AAPL': {
        'vwap': {'k': 2.0},
        'reversal': {'conf_threshold': 50},
        'volume_profile': {'shape_window': 72, 'vol_threshold': 1.0}
    },
    # PROVISIONAL — copied from QQQ for pipeline validation. Run optimizer.optimize_* per-strategy on NVDA before treating p-values as meaningful.
    'NVDA': {
        'vwap': {'k': 1.0},
        'volume_profile': {'shape_window': 24, 'vol_threshold': 0.5}
    }
}

# Weights computed dynamically as w_i = (1 - p_i) / sum(1 - p_j) for available strategies (Option A)
TICKER_WEIGHTS = {
    'MARA': {
        'vwap': 0.5003,
        'volume_profile': 0.4575,
        'reversal': 0.0422
    },
    'CVX': {
        'rsi': 0.5022,
        'vwap': 0.3368,
        'reversal': 0.1175,
        'volume_profile': 0.0435
    },
    'SPY': {
        'volume_profile': 0.8303,
        'vwap': 0.1697
    },
    'QQQ': {
        'volume_profile': 0.7368,
        'vwap': 0.2632
    },
    'AAPL': {
        'reversal': 0.4900,
        'vwap': 0.2776,
        'volume_profile': 0.2324
    },
    # PROVISIONAL — copied from QQQ for pipeline validation.
    'NVDA': {
        'volume_profile': 0.7368,
        'vwap': 0.2632
    }
}

def signal_aggregator_signal(ohlc: pd.DataFrame, ticker: str, threshold: float = 0.5) -> pd.Series:
    """
    Signal Aggregator Strategy Adapter for MCPT (Option A: Missing cells treated as neutral).
    Combines individual strategy signals (RSI, VWAP, Reversal, VP) based on historical p-value weights.
    Returns:
      +1: Combined weighted confidence >= threshold (Buy consensus)
      -1: Combined weighted confidence <= -threshold (Sell consensus)
       0: Neutral
    """
    symbol = ticker.upper()
    n_bars = len(ohlc)
    agg_signal = pd.Series(0, index=ohlc.index)
    
    if symbol not in TICKER_BASE_PARAMS or symbol not in TICKER_WEIGHTS:
        logger.warning(f"Ticker {symbol} not found in aggregator configurations. Returning neutral signals.")
        return agg_signal
        
    try:
        params = TICKER_BASE_PARAMS[symbol]
        weights = TICKER_WEIGHTS[symbol]
        weighted_sum = pd.Series(0.0, index=ohlc.index)
        
        # 1. RSI Signal Contribution
        if 'rsi' in weights and 'rsi' in params:
            sig_rsi = rsi_signal(ohlc, **params['rsi'])
            weighted_sum += sig_rsi * weights['rsi']
            
        # 2. VWAP Signal Contribution
        if 'vwap' in weights and 'vwap' in params:
            sig_vwap = vwap_mean_reversion_signal(ohlc, **params['vwap'])
            weighted_sum += sig_vwap * weights['vwap']
            
        # 3. Reversal Signal Contribution
        if 'reversal' in weights and 'reversal' in params:
            sig_reversal = reversal_signal(ohlc, **params['reversal'])
            weighted_sum += sig_reversal * weights['reversal']
            
        # 4. Volume Profile Signal Contribution
        if 'volume_profile' in weights and 'volume_profile' in params:
            sig_vp = volume_profile_signal(ohlc, **params['volume_profile'])
            weighted_sum += sig_vp * weights['volume_profile']
            
        # 5. Apply Activation Thresholds
        agg_signal.loc[weighted_sum >= threshold] = 1
        agg_signal.loc[weighted_sum <= -threshold] = -1
        
    except Exception as error:
        logger.error(f"Error during signal aggregation for {symbol}: {error}")
        
    return agg_signal
