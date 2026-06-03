import os
import sys
import pandas as pd
import numpy as np
from loguru import logger
from typing import Tuple, Dict, Any

from services.mcpt.optimizer import optimize_rsi, optimize_vwap, optimize_reversal, optimize_volume_profile
from services.mcpt.profit_factor import compute_pf
from services.mcpt.adapters.rsi_adapter import rsi_signal
from services.mcpt.adapters.vwap_adapter import vwap_mean_reversion_signal
from services.mcpt.adapters.reversal_adapter import reversal_signal
from services.mcpt.adapters.volume_profile_adapter import volume_profile_signal
from services.mcpt.costs import PER_FLIP_BPS

def compute_net_returns(signal: pd.Series, returns: pd.Series, bps_cost: float = PER_FLIP_BPS) -> pd.Series:
    """
    Deducts transaction costs (bps_cost) per signal flip from strategy returns.
    Assumes returns is the log return series. Deducting bps_cost represents
    slippage and commission per transaction.
    """
    try:
        strategy_rets = signal * returns
        # Detect signal changes (flips) and scale costs by magnitude
        flip_magnitude = signal.diff().fillna(0).abs()
        net_rets = strategy_rets.copy()
        # Deduct cost on flips
        net_rets -= flip_magnitude * bps_cost
        return net_rets
    except Exception as error:
        logger.error(f"Error computing net returns: {error}")
        return signal * returns

def compute_pf_from_returns(net_returns: pd.Series) -> float:
    """
    Calculates the Profit Factor directly from a series of net returns.
    """
    try:
        pos_sum = float(net_returns[net_returns > 0].sum())
        neg_sum = float(net_returns[net_returns < 0].abs().sum())
        if neg_sum == 0.0:
            return 0.0 if pos_sum == 0.0 else float('inf')
        return pos_sum / neg_sum
    except Exception as error:
        logger.error(f"Error computing profit factor from net returns: {error}")
        return 0.0

def walkforward_aggregator_signal(
    ohlc: pd.DataFrame,
    ticker: str,
    train_lookback: int = 1500,
    train_step: int = 100,
    is_permuted: bool = False,
    real_weights_history: Dict[int, Dict[str, float]] = None,
    min_trades: int = 50,
    n_perms_mini: int = 50,
    real_params_history: Dict[int, Dict[str, Dict[str, Any]]] = None,
    use_equal_weights: bool = True,
    consensus_threshold: float = 0.5
) -> Tuple[pd.Series, Dict[int, Dict[str, float]], Dict[int, Dict[str, Dict[str, Any]]]]:
    """
    Stitches together a rolling Out-of-Sample (OOS) signal for the Signal Aggregator.
    At each retrain step k:
      1. Optimizes parameters for RSI, VWAP, Reversal, and VP strictly inside train window.
      2. If use_equal_weights is True, bypasses mini-MCPT and applies symmetric 0.25 weights to all.
      3. If use_equal_weights is False:
         a. If is_permuted is False, runs a mini in-sample MCPT inside train window to get weights.
         b. If is_permuted is True, reuses cached real weights and params.
      4. Stitches the OOS aggregator signal with a consensus threshold of 0.5.
    Returns:
      (oos_signal_series, weights_history, params_history)
    """
    # Import run_insample_mcpt locally to avoid circular dependency
    from services.mcpt.validator import run_insample_mcpt

    n_bars = len(ohlc)
    oos_signal = pd.Series(0, index=ohlc.index)
    weights_history = {}
    params_history = {}
    
    if n_bars <= train_lookback:
        logger.warning(f"Data length {n_bars} is less than train lookback {train_lookback}. Returning neutral signals.")
        return oos_signal, weights_history, params_history

    try:
        # Step through boundaries
        next_retrain = train_lookback
        active_params = {}
        active_weights = {}

        # Pre-allocate vectorized full-series signals
        sig_series_rsi = pd.Series(0, index=ohlc.index)
        sig_series_vwap = pd.Series(0, index=ohlc.index)
        sig_series_rev = pd.Series(0, index=ohlc.index)
        sig_series_vp = pd.Series(0, index=ohlc.index)

        for i in range(train_lookback, n_bars):
            if i == next_retrain:
                t_start = i - train_lookback
                t_end = i
                train_df = ohlc.iloc[t_start:t_end].copy()
                
                if use_equal_weights:
                    # Equal weights: no in-window MCPT needed, avoiding leakage completely.
                    active_weights = {
                        'rsi': 0.25,
                        'vwap': 0.25,
                        'reversal': 0.25,
                        'volume_profile': 0.25
                    }
                    weights_history[i] = active_weights
                    
                    # Symmetrically optimize strategy parameters on the active training slice (real or permuted!)
                    opt_rsi_p, _ = optimize_rsi(train_df, min_trades=min_trades)
                    opt_vwap_p, _ = optimize_vwap(train_df, min_trades=min_trades)
                    opt_rev_p, _ = optimize_reversal(train_df, min_trades=min_trades)
                    opt_vp_p, _ = optimize_volume_profile(train_df, min_trades=min_trades)
                    
                    active_params = {
                        'rsi': opt_rsi_p,
                        'vwap': opt_vwap_p,
                        'reversal': opt_rev_p,
                        'volume_profile': opt_vp_p
                    }
                    params_history[i] = active_params
                else:
                    if not is_permuted:
                        # 1. Optimize standalone strategies on training slice
                        opt_rsi_p, _ = optimize_rsi(train_df, min_trades=min_trades)
                        opt_vwap_p, _ = optimize_vwap(train_df, min_trades=min_trades)
                        opt_rev_p, _ = optimize_reversal(train_df, min_trades=min_trades)
                        opt_vp_p, _ = optimize_volume_profile(train_df, min_trades=min_trades)
                        
                        active_params = {
                            'rsi': opt_rsi_p,
                            'vwap': opt_vwap_p,
                            'reversal': opt_rev_p,
                            'volume_profile': opt_vp_p
                        }
                        params_history[i] = active_params

                        # 2. Derive weights: Run mini MCPT inside training window to get lookahead-free p-values
                        logger.info(f"Retraining at bar {i}/{n_bars}. Running mini in-window MCPT ({n_perms_mini} perms) to get lookahead-free weights...")
                        
                        p_rsi = run_insample_mcpt('rsi', train_df, n_perms=n_perms_mini, min_trades=min_trades)['p_value']
                        p_vwap = run_insample_mcpt('vwap', train_df, n_perms=n_perms_mini, min_trades=min_trades)['p_value']
                        p_rev = run_insample_mcpt('reversal', train_df, n_perms=n_perms_mini, min_trades=min_trades)['p_value']
                        p_vp = run_insample_mcpt('volume_profile', train_df, n_perms=n_perms_mini, min_trades=min_trades)['p_value']

                        raw_weights = {
                            'rsi': 1.0 - p_rsi,
                            'vwap': 1.0 - p_vwap,
                            'reversal': 1.0 - p_rev,
                            'volume_profile': 1.0 - p_vp
                        }

                        # Option A Dynamic Normalization
                        total_weight = sum(raw_weights.values())
                        if total_weight > 0.0:
                            active_weights = {k: v / total_weight for k, v in raw_weights.items()}
                        else:
                            active_weights = {k: 0.0 for k in raw_weights}
                            
                        weights_history[i] = active_weights
                        logger.info(f"Weights derived at bar {i}: {active_weights}")
                    else:
                        # Permuted run: Reuse both optimized parameters and weights derived on real data
                        if real_params_history and i in real_params_history:
                            active_params = real_params_history[i]
                        else:
                            active_params = {'rsi': {}, 'vwap': {}, 'reversal': {}, 'volume_profile': {}}
                            
                        if real_weights_history and i in real_weights_history:
                            active_weights = real_weights_history[i]
                        else:
                            active_weights = {'rsi': 0.25, 'vwap': 0.25, 'reversal': 0.25, 'volume_profile': 0.25}

                # 3. Vectorized generation for the entire ohlc DataFrame using the active parameters.
                sig_series_rsi = rsi_signal(ohlc, **active_params['rsi'])
                sig_series_vwap = vwap_mean_reversion_signal(ohlc, **active_params['vwap'])
                sig_series_rev = reversal_signal(ohlc, **active_params['reversal'])
                sig_series_vp = volume_profile_signal(ohlc, **active_params['volume_profile'])

                next_retrain += train_step

            # Generate out-of-sample signal at step i using parameters and weights optimized at the retrain boundary.
            weighted_sum = 0.0
            
            if active_weights.get('rsi', 0.0) > 0.0:
                weighted_sum += sig_series_rsi.iloc[i] * active_weights['rsi']
                
            if active_weights.get('vwap', 0.0) > 0.0:
                weighted_sum += sig_series_vwap.iloc[i] * active_weights['vwap']
                
            if active_weights.get('reversal', 0.0) > 0.0:
                weighted_sum += sig_series_rev.iloc[i] * active_weights['reversal']
                
            if active_weights.get('volume_profile', 0.0) > 0.0:
                weighted_sum += sig_series_vp.iloc[i] * active_weights['volume_profile']

            # Apply consensus threshold
            if weighted_sum >= consensus_threshold:
                oos_signal.iloc[i] = 1
            elif weighted_sum <= -consensus_threshold:
                oos_signal.iloc[i] = -1
            else:
                oos_signal.iloc[i] = 0

    except Exception as error:
        logger.error(f"Error during walk-forward aggregator signal stitching: {error}")
        
    return oos_signal, weights_history, params_history
