import json
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from core.database import SessionLocal, Prediction, SystemState
from loguru import logger

MIN_RESOLVED = 50
MIN_WIN_RATE = 0.60
MIN_PROFIT_FACTOR = 1.3
ROLLING_WINDOW_DAYS = 30

# Honest benchmark (deep_research_20260805): equities close up ~53.5% of days, so an
# always-UP caller scores 53.5% direction-only for free. Skill = win_rate MINUS this,
# not minus 50%. Down calls are where the benchmark scores 0 — reported separately.
BENCHMARK_ALWAYS_UP = 0.535

# Transaction-cost model written by scripts/snapshot_spreads.py (live bid/ask spreads).
# Used to report expectancy AFTER costs in bps — hit rate alone hides spread bleed.
COSTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "costs.json")
FALLBACK_ROUND_TRIP_BPS = 5.0


def _load_costs() -> dict:
    try:
        with open(COSTS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _round_trip_bps(costs: dict, symbol: str) -> float:
    sym = costs.get("symbols", {}).get((symbol or "").upper())
    if sym and sym.get("round_trip_bps") is not None:
        return float(sym["round_trip_bps"])
    return float(costs.get("default_round_trip_bps", FALLBACK_ROUND_TRIP_BPS))

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
        from sqlalchemy import or_
        cutoff = datetime.utcnow() - timedelta(days=ROLLING_WINDOW_DAYS)
        resolved = session.query(Prediction).filter(
            Prediction.outcome.in_(["RIGHT", "WRONG"]),
            Prediction.created_at >= cutoff,
            # Exclude the informational position-watch track (single-stock reads on the
            # user's real holdings, explicitly NOT a trade signal) — it must not pollute
            # the ETF experiment's headline edge. NULL category = legacy, keep.
            or_(Prediction.category.is_(None), Prediction.category != "position_watch"),
        ).all()

        n = len(resolved)
        if n == 0:
            return {
                "n_resolved": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "thresholds": {"n_min": MIN_RESOLVED, "wr_min": MIN_WIN_RATE, "pf_min": MIN_PROFIT_FACTOR},
                "unlocked_eligible": False,
            }

        # Headline win/loss split = direction-only (kf decision 2026-07-30): UP wins on any
        # positive close, DOWN on any negative. The stored outcome (RIGHT = |move| > 0.3%)
        # is kept as the secondary "meaningful move" rate. Fallback to the stored outcome
        # when actual_move_pct is missing (legacy rows). The unlock thresholds below are
        # now measured on the direction-only scale — same scale as the 53% goal.
        from services.prediction_service import direction_hit

        def _is_win(p):
            hit = direction_hit(p.direction, p.actual_move_pct)
            return (p.outcome == "RIGHT") if hit is None else hit

        wins = [p for p in resolved if _is_win(p)]
        losses = [p for p in resolved if not _is_win(p)]

        # Win rate (headline, direction-only) + meaningful-move rate (0.3% bar, secondary)
        win_rate = len(wins) / n
        win_rate_meaningful = len([p for p in resolved if p.outcome == "RIGHT"]) / n

        # Down-call split: the always-UP benchmark scores 0% here, so this is the
        # cleanest read of real skill. Small n — report, don't gate on it yet.
        down_calls = [p for p in resolved if p.direction == "DOWN"]
        down_wins = [p for p in down_calls if _is_win(p)]
        down_stats = {
            "n": len(down_calls),
            "win_rate": round(len(down_wins) / len(down_calls), 4) if down_calls else None,
        }

        # Skip-rule measurement (research point 4: "fewer, bigger-conviction trades").
        # NOT enforced — kf's design posts all 4 focus calls daily. This measures what
        # a conf>=60 skip rule WOULD have scored, so the decision can be made on data.
        HIGH_CONF = 60.0
        hi = [p for p in resolved if (p.confidence or 0) >= HIGH_CONF]
        hi_wins = [p for p in hi if _is_win(p)]
        high_conviction = {
            "threshold": HIGH_CONF,
            "n": len(hi),
            "win_rate": round(len(hi_wins) / len(hi), 4) if hi else None,
        }

        # Brier score (calibration): p = broadcast confidence for the predicted
        # direction, y = 1 if direction hit. Lower is better; 0.25 = coin flip.
        # Baseline = always-UP at p=BENCHMARK_ALWAYS_UP for the same rows.
        briers, briers_base = [], []
        for p in resolved:
            y = 1.0 if _is_win(p) else 0.0
            prob = min(max((p.confidence or 50.0) / 100.0, 0.05), 0.95)
            briers.append((prob - y) ** 2)
            y_up = y if p.direction == "UP" else 1.0 - y
            briers_base.append((BENCHMARK_ALWAYS_UP - y_up) ** 2)
        brier = round(sum(briers) / n, 4)
        brier_baseline = round(sum(briers_base) / n, 4)

        # Expectancy AFTER costs, in bps/trade: signed direction-relative move minus
        # per-symbol round-trip spread+fees. Hit rate hides this bleed entirely.
        costs = _load_costs()
        net_bps_list = []
        for p in resolved:
            if p.actual_move_pct is None:
                continue
            signed_pct = p.actual_move_pct if p.direction == "UP" else -p.actual_move_pct
            net_bps_list.append(signed_pct * 100.0 - _round_trip_bps(costs, p.symbol))
        expectancy_bps = (round(sum(net_bps_list) / len(net_bps_list), 2)
                          if net_bps_list else None)
        avg_cost_bps = (round(sum(_round_trip_bps(costs, p.symbol) for p in resolved) / n, 2))

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
            "win_rate": round(win_rate, 4),                      # headline: direction-only
            "win_rate_meaningful": round(win_rate_meaningful, 4),  # |move| > 0.3% bar
            "benchmark_always_up": BENCHMARK_ALWAYS_UP,
            "wr_vs_benchmark": round(win_rate - BENCHMARK_ALWAYS_UP, 4),
            "down_calls": down_stats,
            "high_conviction": high_conviction,
            "brier": brier,                       # model calibration (lower better)
            "brier_always_up": brier_baseline,    # beat this or confidence is noise
            "expectancy_after_costs_bps": expectancy_bps,
            "avg_cost_bps": avg_cost_bps,
            "cost_model_session": costs.get("session", "fallback"),
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
