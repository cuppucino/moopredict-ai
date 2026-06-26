import pandas as pd
import numpy as np
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor
from loguru import logger

from services._legacy.mcpt.bar_permute import get_permutation
from services._legacy.mcpt.optimizer import optimize_rsi, optimize_vwap, optimize_reversal, optimize_volume_profile
from services._legacy.mcpt.profit_factor import compute_pf
from services._legacy.mcpt.adapters.rsi_adapter import rsi_signal
from services._legacy.mcpt.adapters.vwap_adapter import vwap_mean_reversion_signal
from services._legacy.mcpt.adapters.reversal_adapter import reversal_signal
from services._legacy.mcpt.adapters.volume_profile_adapter import volume_profile_signal
from services._legacy.mcpt.adapters.signal_aggregator_adapter import signal_aggregator_signal
from services._legacy.mcpt.walkforward import walkforward_aggregator_signal, compute_net_returns, compute_pf_from_returns
from services._legacy.mcpt.costs import PER_FLIP_BPS

def count_signal_flips(signal: pd.Series) -> int:
    return int((signal.diff().fillna(0) != 0).sum())

def _run_single_perm(strategy: str, ohlc_log_close: pd.Series, ohlc_df: pd.DataFrame, seed: int, min_trades: int = 50, ticker: str = None):
    try:
        # This is a bit tricky to multiprocessing, because get_permutation takes a DataFrame.
        # Let's just pass the DataFrame and seed.
        perm_df = get_permutation(ohlc_df, seed=seed)
        perm_df['r'] = np.log(perm_df['close']).diff().shift(-1)
        
        if strategy == 'rsi':
            best_params, perm_pf = optimize_rsi(perm_df, min_trades=min_trades)
            sig = rsi_signal(perm_df, **best_params)
        elif strategy == 'vwap':
            best_params, perm_pf = optimize_vwap(perm_df, min_trades=min_trades)
            sig = vwap_mean_reversion_signal(perm_df, **best_params)
        elif strategy == 'reversal':
            best_params, perm_pf = optimize_reversal(perm_df, min_trades=min_trades)
            sig = reversal_signal(perm_df, **best_params)
        elif strategy == 'volume_profile':
            best_params, perm_pf = optimize_volume_profile(perm_df, min_trades=min_trades)
            sig = volume_profile_signal(perm_df, **best_params)
        elif strategy == 'signal_aggregator':
            best_params = {'threshold': 0.5}
            sig = signal_aggregator_signal(perm_df, ticker=ticker, threshold=0.5)
            perm_pf = compute_pf(sig, perm_df['r'])
        else:
            return -1.0, 0
            
        trades = count_signal_flips(sig)
        return perm_pf, trades
    except Exception as e:
        return -1.0, 0

def run_insample_mcpt(strategy: str, ohlc: pd.DataFrame, n_perms: int = 1000, min_trades: int = 50, ticker: str = None):
    if strategy not in ['rsi', 'vwap', 'reversal', 'volume_profile', 'signal_aggregator']:
        raise ValueError(f"Strategy {strategy} not supported yet.")
        
    ohlc = ohlc.copy()
    ohlc['r'] = np.log(ohlc['close']).diff().shift(-1)
    
    # 1. Real Optimization
    if strategy == 'rsi':
        best_params, real_pf = optimize_rsi(ohlc, min_trades=min_trades)
        real_sig = rsi_signal(ohlc, **best_params)
    elif strategy == 'vwap':
        best_params, real_pf = optimize_vwap(ohlc, min_trades=min_trades)
        real_sig = vwap_mean_reversion_signal(ohlc, **best_params)
    elif strategy == 'reversal':
        best_params, real_pf = optimize_reversal(ohlc, min_trades=min_trades)
        real_sig = reversal_signal(ohlc, **best_params)
    elif strategy == 'volume_profile':
        best_params, real_pf = optimize_volume_profile(ohlc, min_trades=min_trades)
        real_sig = volume_profile_signal(ohlc, **best_params)
    elif strategy == 'signal_aggregator':
        if ticker is None:
            raise ValueError("ticker must be specified for signal_aggregator strategy.")
        best_params = {'threshold': 0.5}
        real_sig = signal_aggregator_signal(ohlc, ticker=ticker, threshold=0.5)
        real_pf = compute_pf(real_sig, ohlc['r'])
        
    n_trades = count_signal_flips(real_sig)
    
    if n_trades < min_trades:
        return {
            "strategy": strategy,
            "best_params": best_params,
            "real_pf": real_pf,
            "p_value": 1.0,
            "n_trades": n_trades,
            "message": "INSUFFICIENT_TRADES"
        }
        
    # 2. Permutation Testing
    perm_better_count = 1 # Start at 1
    permuted_pfs = []
    permuted_trades = []
    
    print(f"Running In-sample MCPT for {strategy} with {n_perms} perms and min_trades={min_trades}...")
    
    # We will use multiprocessing to speed this up
    seeds = list(range(42, 42 + n_perms - 1))
    
    if n_perms <= 10:
        # Run sequentially to avoid macOS process spawning overhead on small runs (e.g. unit tests)
        for seed in seeds:
            perm_pf, p_trades = _run_single_perm(strategy, None, ohlc, seed, min_trades, ticker)
            permuted_pfs.append(perm_pf)
            permuted_trades.append(p_trades)
            if perm_pf >= real_pf:
                perm_better_count += 1
    else:
        # Run in parallel using ProcessPoolExecutor for large permutation sizes
        with ProcessPoolExecutor(max_workers=8) as executor:
            futures = []
            for seed in seeds:
                futures.append(executor.submit(_run_single_perm, strategy, None, ohlc, seed, min_trades, ticker))
                
            for future in tqdm(futures, total=len(seeds)):
                perm_pf, p_trades = future.result()
                permuted_pfs.append(perm_pf)
                permuted_trades.append(p_trades)
                if perm_pf >= real_pf:
                    perm_better_count += 1
                
    # Re-adjust n_perms in case we dropped some
    valid_perms = len(permuted_pfs) + 1 # +1 for real
    p_value = perm_better_count / valid_perms if valid_perms > 1 else 1.0
    
    permuted_pfs_sorted = sorted(permuted_pfs)
    p50 = np.percentile(permuted_pfs_sorted, 50) if permuted_pfs_sorted else 0
    p95 = np.percentile(permuted_pfs_sorted, 95) if permuted_pfs_sorted else 0
    
    permuted_trades_sorted = sorted(permuted_trades)
    t_p50 = np.percentile(permuted_trades_sorted, 50) if permuted_trades_sorted else 0
    t_p5 = np.percentile(permuted_trades_sorted, 5) if permuted_trades_sorted else 0
    t_p95 = np.percentile(permuted_trades_sorted, 95) if permuted_trades_sorted else 0
    
    return {
        "strategy": strategy,
        "best_params": best_params,
        "real_pf": real_pf,
        "p_value": p_value,
        "n_trades": n_trades,
        "p50": p50,
        "p95": p95,
        "t_p50": t_p50,
        "t_p5": t_p5,
        "t_p95": t_p95,
        "valid_perms": valid_perms,
        "message": "SUCCESS"
    }

def _run_single_perm_wf(
    ohlc_df: pd.DataFrame,
    seed: int,
    ticker: str,
    train_lookback: int,
    train_step: int,
    real_weights_history: dict,
    min_trades: int,
    real_params_history: dict,
    use_equal_weights: bool = True,
    consensus_threshold: float = 0.5
):
    try:
        # 1. Permute the series starting after train_lookback
        perm_df = get_permutation(ohlc_df, start_index=train_lookback, seed=seed)
        perm_df['r'] = np.log(perm_df['close']).diff().shift(-1)
        
        # 2. Generate stitched walk-forward signal on the permuted series using real weights and params history
        perm_sig, _, _ = walkforward_aggregator_signal(
            perm_df,
            ticker=ticker,
            train_lookback=train_lookback,
            train_step=train_step,
            is_permuted=True,
            real_weights_history=real_weights_history,
            min_trades=min_trades,
            real_params_history=real_params_history,
            use_equal_weights=use_equal_weights,
            consensus_threshold=consensus_threshold
        )
        
        # 3. Deduct transaction costs (10 bps per flip)
        net_rets = compute_net_returns(perm_sig, perm_df['r'], bps_cost=PER_FLIP_BPS)
        
        # 4. Compute PF and flips on OOS portion
        oos_net_rets = net_rets.iloc[train_lookback:]
        perm_pf = compute_pf_from_returns(oos_net_rets)
        trades = count_signal_flips(perm_sig.iloc[train_lookback:])
        
        return perm_pf, trades
    except Exception as error:
        logger.error(f"Error in single perm walk-forward: {error}")
        return -1.0, 0

def run_walkforward_mcpt(
    ohlc: pd.DataFrame,
    ticker: str,
    n_perms: int = 200,
    min_trades: int = 50,
    train_lookback: int = 1500,
    train_step: int = 100,
    n_perms_mini: int = 50,
    use_equal_weights: bool = True,
    consensus_threshold: float = 0.5
):
    try:
        ohlc = ohlc.copy()
        ohlc['r'] = np.log(ohlc['close']).diff().shift(-1)
        
        # 1. Real walk-forward run
        logger.info(f"Running Real Out-of-Sample Walk-Forward for {ticker}...")
        real_sig, real_weights_history, real_params_history = walkforward_aggregator_signal(
            ohlc,
            ticker=ticker,
            train_lookback=train_lookback,
            train_step=train_step,
            is_permuted=False,
            min_trades=min_trades,
            n_perms_mini=n_perms_mini,
            use_equal_weights=use_equal_weights,
            consensus_threshold=consensus_threshold
        )
        
        # Deduct transaction costs (10 bps per flip)
        real_net_rets = compute_net_returns(real_sig, ohlc['r'], bps_cost=PER_FLIP_BPS)
        real_pf = compute_pf_from_returns(real_net_rets.iloc[train_lookback:])
        n_trades = count_signal_flips(real_sig.iloc[train_lookback:])
        
        logger.info(f"Real Out-of-Sample PF (with transaction costs): {real_pf:.4f}")
        logger.info(f"Real Out-of-Sample Trades: {n_trades}")
        
        # Enforce trade floor on the out-of-sample trades
        if n_trades < min_trades:
            return {
                "strategy": "signal_aggregator",
                "real_pf": real_pf,
                "p_value": 1.0,
                "n_trades": n_trades,
                "message": "INSUFFICIENT_TRADES"
            }
            
        # 2. Concurrent permutation runs
        perm_better_count = 1
        permuted_pfs = []
        permuted_trades = []
        
        logger.info(f"Running concurrent Walk-Forward MCPT for {ticker} with {n_perms} permutations...")
        seeds = list(range(42, 42 + n_perms - 1))
        
        with ProcessPoolExecutor(max_workers=8) as executor:
            futures = []
            for seed in seeds:
                futures.append(executor.submit(
                    _run_single_perm_wf,
                    ohlc,
                    seed,
                    ticker,
                    train_lookback,
                    train_step,
                    real_weights_history,
                    min_trades,
                    real_params_history,
                    use_equal_weights,
                    consensus_threshold
                ))
                
            for future in tqdm(futures, total=len(seeds)):
                perm_pf, p_trades = future.result()
                permuted_pfs.append(perm_pf)
                permuted_trades.append(p_trades)
                
                if perm_pf >= real_pf:
                    perm_better_count += 1
                    
        # Re-adjust in case some failed
        valid_perms = len(permuted_pfs) + 1
        p_value = perm_better_count / valid_perms if valid_perms > 1 else 1.0
        
        permuted_pfs_sorted = sorted(permuted_pfs)
        p50 = np.percentile(permuted_pfs_sorted, 50) if permuted_pfs_sorted else 0
        p95 = np.percentile(permuted_pfs_sorted, 95) if permuted_pfs_sorted else 0
        
        return {
            "strategy": "signal_aggregator",
            "real_pf": real_pf,
            "p_value": p_value,
            "n_trades": n_trades,
            "p50": p50,
            "p95": p95,
            "valid_perms": valid_perms,
            "message": "SUCCESS"
        }
    except Exception as error:
        logger.error(f"Error in walk-forward MCPT: {error}")
        return {
            "strategy": "signal_aggregator",
            "real_pf": -1.0,
            "p_value": 1.0,
            "n_trades": 0,
            "p50": 0,
            "p95": 0,
            "valid_perms": 0,
            "message": f"ERROR: {error}"
        }
