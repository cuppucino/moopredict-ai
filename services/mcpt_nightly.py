import os
import sys
from datetime import datetime
from loguru import logger
from sqlalchemy.orm import Session

# Add project root to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import SessionLocal, MCPTResult
from services.notifications import notification_queue

def is_trading_day(dt: datetime) -> bool:
    """Check if the given datetime is a US trading weekday (Monday to Friday)."""
    try:
        return dt.weekday() < 5
    except Exception as error:
        logger.error(f"Error checking trading day: {error}")
        return True

def should_alert(prev_p: float, current_p: float, prev_status: str, current_status: str) -> bool:
    """
    Determines if a notification alert should be triggered based on status transitions
    or crossing key statistical bands (0.05 significance or 0.10 watchlist boundary).
    """
    try:
        if prev_p is None or current_p is None:
            return False
        if prev_status != current_status:           # state change is always actionable
            return True
        if (prev_p < 0.05) != (current_p < 0.05):   # crossed significance gate
            return True
        if (prev_p < 0.10) != (current_p < 0.10):   # crossed watchlist boundary
            return True
        return False
    except Exception as error:
        logger.error(f"Error in should_alert logic: {error}")
        return True

def update_mcpt_status(
    session: Session,
    strategy: str,
    ticker: str,
    insample_p: float,
    wf_p: float,
    real_pf: float
) -> MCPTResult:
    """
    Applies the pre-registered gating rules and 5-day EMA smoothing hysteresis
    to determine and store the strategy status in PostgreSQL.
    """
    try:
        # 1. Fetch previous results to compute EMA
        prev_results = session.query(MCPTResult).filter(
            MCPTResult.strategy == strategy,
            MCPTResult.ticker == ticker
        ).order_by(MCPTResult.run_at.desc()).all()
        
        total_runs = len(prev_results) + 1
        
        # 2. Compute 5-day EMA of walk-forward p-value (alpha = 0.2)
        alpha = 0.2
        if prev_results:
            last_ema = prev_results[0].ema_p
            ema_p = alpha * wf_p + (1 - alpha) * last_ema
            prev_status = prev_results[0].status
            logger.info(f"Loaded prior history for {strategy}-{ticker}. Last EMA: {last_ema:.4f}, Prev Status: {prev_status}")
        else:
            ema_p = wf_p
            prev_status = 'DISABLED'
            logger.info(f"No prior history found for {strategy}-{ticker}. Initializing EMA with current p-value: {wf_p:.4f}")
            
        # 3. Apply Pre-registered Gating Criteria
        #   - TRADE (LIVE): insample_p < 0.01 AND walkforward_p < 0.05
        #   - WATCHLIST: only one passes
        #   - DISABLED: both fail
        both_pass = (insample_p < 0.01) and (wf_p < 0.05)
        one_passes = (insample_p < 0.01) or (wf_p < 0.05)
        
        if total_runs < 5:
            # Bootstrap rule: Freeze status as WATCHLIST until N >= 5 runs have accumulated
            if both_pass or one_passes:
                status = 'WATCHLIST'
            else:
                status = 'DISABLED'
            logger.info(f"Bootstrap in progress ({total_runs}/5 runs). Gating status frozen as {status}.")
        else:
            if both_pass:
                # Hysteresis rule: Enable trading (LIVE) when EMA_p < 0.04
                if ema_p < 0.04:
                    status = 'LIVE'
                else:
                    status = 'WATCHLIST'
            elif one_passes:
                status = 'WATCHLIST'
            else:
                status = 'DISABLED'
                
            # Hysteresis rule: If previously LIVE, stay LIVE until EMA_p > 0.08
            if prev_status == 'LIVE' and ema_p <= 0.08:
                status = 'LIVE'
        
        # 4. Trigger Alerts on State Changes or significance gate crossings
        if prev_results:
            prev_record = prev_results[0]
            if should_alert(prev_record.wf_p, wf_p, prev_record.status, status):
                msg = (
                    f"🚨 *MCPT STATUS ALERT: {strategy}-{ticker}*\n"
                    f"──────────────────\n"
                    f"Transition: *{prev_record.status}* ➔ *{status}*\n"
                    f"P-value: {(prev_record.wf_p if prev_record.wf_p is not None else 0.0):.4f} ➔ {wf_p:.4f}\n"
                    f"EMA P-value: {(prev_record.ema_p if prev_record.ema_p is not None else 0.0):.4f} ➔ {ema_p:.4f}\n"
                    f"Significance Gate (0.05): {'Crossed' if (prev_record.wf_p is not None and (prev_record.wf_p < 0.05) != (wf_p < 0.05)) else 'Unchanged'}\n"
                    f"Watchlist Gate (0.10): {'Crossed' if (prev_record.wf_p is not None and (prev_record.wf_p < 0.10) != (wf_p < 0.10)) else 'Unchanged'}\n"
                    f"Warmup Status: {'Complete' if total_runs >= 5 else f'Warmup ({total_runs}/5 runs)'}"
                )
                logger.warning(f"[MCPT Alert] {msg}")
                notification_queue.enqueue(msg, level="warning", category="mcpt")
            
        # 5. Save to database
        new_record = MCPTResult(
            strategy=strategy,
            ticker=ticker,
            run_at=datetime.utcnow(),
            insample_p=insample_p,
            wf_p=wf_p,
            ema_p=ema_p,
            real_pf=real_pf,
            status=status
        )
        session.add(new_record)
        session.commit()
        logger.info(f"Successfully recorded MCPT result: {strategy}-{ticker} | status={status} | insample_p={insample_p:.4f} | wf_p={wf_p:.4f} | ema_p={ema_p:.4f}")
        return new_record
        
    except Exception as error:
        session.rollback()
        logger.error(f"Error updating nightly MCPT status: {error}")
        raise error

def seed_mara_aggregator():
    """
    Seeds the first row of MCPTResult for MARA Signal Aggregator as 'WATCHLIST',
    honoring the pre-registered gate and ensuring absolute architectural integrity.
    """
    session = SessionLocal()
    try:
        # Check if already seeded
        exists = session.query(MCPTResult).filter(
            MCPTResult.strategy == 'signal_aggregator',
            MCPTResult.ticker == 'MARA'
        ).first()
        
        if exists:
            logger.info("MARA Signal Aggregator seeding skipped: record already exists.")
            return
            
        logger.info("Seeding first confirmatory walk-forward row for MARA Signal Aggregator...")
        
        # Seed record (insample_p = 0.0050, wf_p = 0.0600, real_pf = 1.3591, status = WATCHLIST)
        new_record = MCPTResult(
            strategy='signal_aggregator',
            ticker='MARA',
            run_at=datetime.utcnow(),
            insample_p=0.0050,
            wf_p=0.0600,
            ema_p=0.0600,
            real_pf=1.3591,
            status='WATCHLIST'
        )
        session.add(new_record)
        session.commit()
        logger.info("Successfully seeded first validation row for MARA Signal Aggregator.")
        
    except Exception as error:
        session.rollback()
        logger.error(f"Error seeding MARA aggregator: {error}")
    finally:
        session.close()

def run_nightly_job():
    """
    Main execution wrapper for the nightly MCPT job, checking trading-day constraints,
    loading fresh daily data via yfinance, and executing in-sample and walk-forward MCPT validations.
    """
    from services.mcpt.validator import run_insample_mcpt, run_walkforward_mcpt
    import yfinance as yf
    
    try:
        today = datetime.utcnow()
        if not is_trading_day(today):
            logger.info("Non-trading day, skipping MCPT recompute.")
            return False
            
        logger.info("Executing nightly MCPT status refresh...")
        session = SessionLocal()
        
        # We will retrieve tickers from TICKER_BASE_PARAMS / TICKER_WEIGHTS
        tickers = ['MARA', 'CVX', 'SPY', 'QQQ', 'AAPL', 'NVDA']
        strategy = 'signal_aggregator'
        
        # Use env variable to allow fast dry runs during testing
        n_perms_wf = int(os.getenv("MCPT_PERMS_WF", "200"))
        n_perms_is = int(os.getenv("MCPT_PERMS_IS", "1000"))
        n_perms_mini = int(os.getenv("MCPT_PERMS_MINI", "50"))
        
        logger.info(f"Nightly settings: perms_wf={n_perms_wf}, perms_is={n_perms_is}, perms_mini={n_perms_mini}")
        
        for ticker in tickers:
            try:
                logger.info(f"Fetching historical data for {ticker}...")
                # Download daily data. Walkforward lookback is 1500 daily bars, which is ~6 years of trading days.
                # Download 8 years to ensure we have ample data.
                df = yf.download(ticker, period="8y", interval="1d", progress=False)
                if df.empty or len(df) < 1600:
                    data_len = 0 if df.empty else len(df)
                    msg = f"[MCPT] Skipping {ticker}: insufficient data ({data_len} bars < 1600 minimum). Investigate yfinance feed."
                    logger.error(msg)
                    notification_queue.enqueue(msg, level="warning", category="mcpt")
                    continue
                    
                # Format column names to lowercase for adapters, handling yfinance MultiIndex tuples
                df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
                
                # Check if it has the required columns
                required_cols = ['open', 'high', 'low', 'close', 'volume']
                if not all(col in df.columns for col in required_cols):
                    logger.warning(f"Data for {ticker} is missing required columns. Had: {df.columns}. Skipping.")
                    continue
                
                logger.info(f"Running In-sample MCPT for {strategy}-{ticker}...")
                insample_res = run_insample_mcpt(strategy, df, n_perms=n_perms_is, min_trades=10, ticker=ticker)
                
                logger.info(f"Running Walk-forward MCPT for {strategy}-{ticker}...")
                wf_res = run_walkforward_mcpt(
                    df,
                    ticker=ticker,
                    n_perms=n_perms_wf,
                    min_trades=10,
                    train_lookback=1500,
                    train_step=100,
                    n_perms_mini=n_perms_mini,
                    use_equal_weights=True,
                    consensus_threshold=0.5
                )
                
                if insample_res['message'] == 'SUCCESS' and wf_res['message'] == 'SUCCESS':
                    update_mcpt_status(
                        session=session,
                        strategy=strategy,
                        ticker=ticker,
                        insample_p=insample_res['p_value'],
                        wf_p=wf_res['p_value'],
                        real_pf=wf_res['real_pf']
                    )
                else:
                    logger.warning(
                        f"MCPT did not succeed for {strategy}-{ticker}. "
                        f"Insample msg: {insample_res['message']}, WF msg: {wf_res['message']}"
                    )
            except Exception as ticker_error:
                logger.error(f"Error executing nightly MCPT for {ticker}: {ticker_error}")
                
        session.close()
        return True
    except Exception as error:
        logger.error(f"Nightly MCPT job execution failed: {error}")
        return False

if __name__ == "__main__":
    run_nightly_job()
    seed_mara_aggregator()

