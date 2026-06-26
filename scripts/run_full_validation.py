#!/usr/bin/env python
"""
Full MCPT Re-validation Script
Runs all strategies across all tickers at production fidelity.
Strategies: RSI, VWAP, reversal, volume_profile, signal_aggregator
Tickers: MARA, CVX, SPY, QQQ, AAPL, NVDA
Fidelity: MCPT_PERMS_IS=1000, MCPT_PERMS_WF=200
"""

import os
import sys
from datetime import datetime
from loguru import logger

# Add project root to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import yfinance as yf
from core.database import SessionLocal, MCPTResult
from services._legacy.mcpt.validator import run_insample_mcpt, run_walkforward_mcpt

# Production fidelity
MCPT_PERMS_IS = int(os.getenv("MCPT_PERMS_IS", "1000"))
MCPT_PERMS_WF = int(os.getenv("MCPT_PERMS_WF", "200"))
MIN_TRADES = 10

# All strategies (only signal_aggregator is enabled for walk-forward validation in this phase)
STRATEGIES = ['signal_aggregator']

# All tickers including NVDA (respects TICKERS environment variable if provided)
env_tickers = os.getenv("TICKERS")
if env_tickers:
    TICKERS = [t.strip().upper() for t in env_tickers.split(",") if t.strip()]
else:
    TICKERS = ['MARA', 'CVX', 'SPY', 'QQQ', 'AAPL', 'NVDA']


def update_mcpt_status(session, strategy, ticker, insample_p, wf_p, real_pf):
    """Apply gating rules and store result."""
    from services._legacy.mcpt_nightly import update_mcpt_status as _update
    return _update(session, strategy, ticker, insample_p, wf_p, real_pf)


def run_strategy_validation(strategy, ticker, session):
    """Run full validation for a single strategy × ticker pair."""
    try:
        logger.info(f"\n{'='*60}")
        logger.info(f"Starting: {strategy.upper()} × {ticker}")
        logger.info(f"{'='*60}")
        
        # Skip individual strategies with a clear log
        if strategy != 'signal_aggregator':
            logger.warning(f"Skipping {strategy.upper()}: Individual-strategy walk-forward not yet implemented — pending Phase 5.")
            return None
        
        # Download 8 years of data
        df = yf.download(ticker, period="8y", interval="1d", progress=False)
        if df.empty:
            raise ValueError(f"No daily bars downloaded for {ticker}.")
            
        n_bars = len(df)
        if n_bars < 1600:
            raise ValueError(f"Insufficient daily bars for walk-forward of {ticker}: got {n_bars}, required at least 1600 bars.")
            
        # Format column names
        df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
        
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required_cols):
            logger.warning(f"Missing required columns for {ticker}. Had: {df.columns}")
            return None
        
        # In-sample MCPT
        logger.info(f"Running In-sample MCPT for {strategy}-{ticker} (perms={MCPT_PERMS_IS})...")
        insample_res = run_insample_mcpt(
            strategy, 
            df, 
            n_perms=MCPT_PERMS_IS, 
            min_trades=MIN_TRADES, 
            ticker=ticker
        )
        
        # Walk-forward MCPT
        logger.info(f"Running Walk-forward MCPT for {strategy}-{ticker} (perms={MCPT_PERMS_WF})...")
        wf_res = run_walkforward_mcpt(
            df,
            ticker=ticker,
            n_perms=MCPT_PERMS_WF,
            min_trades=MIN_TRADES,
            train_lookback=1500,
            train_step=100,
            n_perms_mini=50,
            use_equal_weights=True,
            consensus_threshold=0.5
        )
        
        # Check results
        if insample_res['message'] != 'SUCCESS':
            logger.warning(f"In-sample MCPT failed for {strategy}-{ticker}: {insample_res['message']}")
            return None
            
        if wf_res['message'] != 'SUCCESS':
            logger.warning(f"Walk-forward MCPT failed for {strategy}-{ticker}: {wf_res['message']}")
            return None
        
        # Store results
        insample_p = insample_res['p_value']
        wf_p = wf_res['p_value']
        real_pf = wf_res['real_pf']
        
        update_mcpt_status(session, strategy, ticker, insample_p, wf_p, real_pf)
        
        result = {
            'strategy': strategy,
            'ticker': ticker,
            'insample_p': insample_p,
            'wf_p': wf_p,
            'real_pf': real_pf,
            'status': 'COMPLETED'
        }
        
        logger.info(f"✅ Completed: {strategy.upper()} × {ticker}")
        logger.info(f"   In-sample p: {insample_p:.4f}")
        logger.info(f"   Walk-forward p: {wf_p:.4f}")
        logger.info(f"   Real PF: {real_pf:.4f}")
        
        return result
        
    except ValueError as error:
        raise error
    except Exception as error:
        logger.error(f"Error validating {strategy}-{ticker}: {error}")
        return None


def main():
    """Run full validation across all strategies and tickers."""
    logger.info("="*60)
    logger.info("FULL MCPT RE-VALIDATION")
    logger.info(f"Started: {datetime.utcnow().isoformat()}")
    logger.info(f"Strategies: {STRATEGIES}")
    logger.info(f"Tickers: {TICKERS}")
    logger.info(f"Total combinations: {len(STRATEGIES) * len(TICKERS)}")
    logger.info(f"Fidelity: IS={MCPT_PERMS_IS}, WF={MCPT_PERMS_WF}")
    logger.info("="*60)
    
    session = SessionLocal()
    results = []
    
    try:
        for strategy in STRATEGIES:
            for ticker in TICKERS:
                result = run_strategy_validation(strategy, ticker, session)
                if result:
                    results.append(result)
        
        # Summary table
        logger.info("\n" + "="*60)
        logger.info("RESULTS SUMMARY")
        logger.info("="*60)
        
        # Print as markdown table
        print("\n| Strategy | Ticker | In-sample p | WF p | Real PF |")
        print("|----------|--------|-------------|------|---------|")
        
        for r in results:
            print(f"| {r['strategy']} | {r['ticker']} | {r['insample_p']:.4f} | {r['wf_p']:.4f} | {r['real_pf']:.4f} |")
        
        print("\n*Note: Database status is the sole source of truth (incorporates 5-day EMA smoothing, bootstrap rules, and hysteresis).*")
        
        logger.info(f"\nCompleted: {datetime.utcnow().isoformat()}")
        logger.info(f"Total validated: {len(results)}/{len(STRATEGIES) * len(TICKERS)}")
        
    finally:
        session.close()
    
    return results


if __name__ == "__main__":
    main()