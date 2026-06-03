from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from core.database import SessionLocal, Prediction, SystemState
from loguru import logger

MIN_RESOLVED = 50
MIN_WIN_RATE = 0.60
MIN_PROFIT_FACTOR = 1.3
ROLLING_WINDOW_DAYS = 30

def pct_pnl(p) -> float:
    """
    Helper to calculate percentage move magnitude at exit.
    Prioritizes actual_move_pct, falls back to target/entry price, defaults to 0.0.
    """
    try:
        if p.actual_move_pct is not None:
            return abs(p.actual_move_pct) / 100.0
        
        # Fallback if actual_move_pct is not resolved but target_price/entry_price exist
        if p.target_price and p.entry_price and p.entry_price > 0:
            return abs((p.target_price - p.entry_price) / p.entry_price)
        
        return 0.0
    except Exception as e:
        logger.error(f"[TrackRecord] Error calculating pct_pnl for prediction #{p.id}: {e}")
        return 0.0

def compute_metrics(session: Session) -> dict:
    """Compute win rate + profit factor + count over rolling 30d."""
    try:
        cutoff = datetime.utcnow() - timedelta(days=ROLLING_WINDOW_DAYS)
        resolved = session.query(Prediction).filter(
            Prediction.outcome.in_(["RIGHT", "WRONG"]),
            Prediction.created_at >= cutoff,
        ).all()

        n = len(resolved)
        if n == 0:
            return {
                "n_resolved": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "thresholds": {"n_min": MIN_RESOLVED, "wr_min": MIN_WIN_RATE, "pf_min": MIN_PROFIT_FACTOR},
                "unlocked_eligible": False,
            }

        wins = [p for p in resolved if p.outcome == "RIGHT"]
        losses = [p for p in resolved if p.outcome == "WRONG"]

        # Win rate
        win_rate = len(wins) / n

        # Profit factor
        gross_win = sum(pct_pnl(p) for p in wins)
        gross_loss = sum(pct_pnl(p) for p in losses)
        
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        # Float representation of inf for JSON serialization
        pf_val = pf
        if pf == float("inf"):
            pf_val = 999999.0  # safe representation for JSON / calculations

        return {
            "n_resolved": n,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(pf_val, 4),
            "thresholds": {
                "n_min": MIN_RESOLVED,
                "wr_min": MIN_WIN_RATE,
                "pf_min": MIN_PROFIT_FACTOR,
            },
            "unlocked_eligible": (
                n >= MIN_RESOLVED
                and win_rate >= MIN_WIN_RATE
                and pf_val >= MIN_PROFIT_FACTOR
            ),
            "window_days": ROLLING_WINDOW_DAYS,
            "computed_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"[TrackRecord] Error computing metrics: {e}")
        return {
            "n_resolved": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "thresholds": {"n_min": MIN_RESOLVED, "wr_min": MIN_WIN_RATE, "pf_min": MIN_PROFIT_FACTOR},
            "unlocked_eligible": False,
        }

def update_unlock_state(session: Session, metrics: dict) -> None:
    """Set SystemState.real_trading_unlocked based on metrics; log transitions."""
    try:
        state = session.query(SystemState).filter(SystemState.key == "real_trading_unlocked").first()
        if state is None:
            state = SystemState(
                key="real_trading_unlocked",
                real_trading_unlocked=False,
                unlock_history=[]
            )
            session.add(state)
            session.commit()
            state = session.query(SystemState).filter(SystemState.key == "real_trading_unlocked").first()

        desired = metrics["unlocked_eligible"]
        if state.real_trading_unlocked != desired:
            transition = "UNLOCKED" if desired else "RELOCKED"
            state.unlock_history = (state.unlock_history or []) + [{
                "timestamp": metrics["computed_at"],
                "transition": transition,
                "metrics": metrics,
            }]
            state.real_trading_unlocked = desired
            
            # Alert via notifications
            try:
                from services.notifications import notification_queue
                notification_queue.enqueue(
                    f"🔓 REAL TRADING {transition}: n={metrics['n_resolved']}, "
                    f"wr={metrics['win_rate']:.2%}, pf={metrics['profit_factor']:.2f}",
                    level="alert", category="track_record"
                )
            except Exception as ne:
                logger.error(f"[TrackRecord] Notification enqueue failed: {ne}")
                
        session.commit()
    except Exception as e:
        logger.error(f"[TrackRecord] Error in update_unlock_state: {e}")
        session.rollback()

def run_daily_job():
    """Cron entry: compute + update + log."""
    session = SessionLocal()
    try:
        metrics = compute_metrics(session)
        update_unlock_state(session, metrics)
        logger.info(f"[TrackRecord] {metrics}")
    except Exception as e:
        logger.error(f"[TrackRecord] Daily job failed: {e}")
    finally:
        session.close()
