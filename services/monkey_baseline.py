import random
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger
from core.database import SessionLocal, Prediction, UserWatchlist
from services.moomoo_service import moomoo_service

class MonkeyBaseline:
    """
    A Monte Carlo random-trade simulator to generate a baseline for comparison.
    Rules:
    - Symbols: Watchlist only (as requested by user)
    - Sizing: Same as MooPredict (conceptually equal weight $1000 for stats)
    """

    def run_simulation(self, symbols: List[str], num_trials: int = 100, holding_days: int = 7) -> Dict:
        """Run N random trades and compute aggregate stats."""
        if not symbols:
            return {"error": "No symbols provided for simulation."}

        results = []
        logger.info(f"[Monkey] Starting simulation for {len(symbols)} symbols, {num_trials} trials.")

        for i in range(num_trials):
            symbol = random.choice(symbols)
            # Random direction: UP or DOWN
            direction = random.choice(["UP", "DOWN"])
            
            # Random entry date in the last 6 months (excluding the last holding_days)
            end_date = datetime.now() - timedelta(days=holding_days + 1)
            start_date = end_date - timedelta(days=180)
            
            random_days = random.randint(0, 180)
            entry_date = start_date + timedelta(days=random_days)
            exit_date = entry_date + timedelta(days=holding_days)
            
            try:
                # Use yfinance for historical data
                data = yf.download(symbol, start=entry_date.strftime('%Y-%m-%d'), 
                                   end=(exit_date + timedelta(days=5)).strftime('%Y-%m-%d'), 
                                   progress=False)
                
                if data.empty or len(data) < 2:
                    continue
                
                entry_price = float(data.iloc[0]['Close'])
                exit_price = float(data.iloc[-1]['Close'])
                
                move_pct = ((exit_price / entry_price) - 1) * 100
                if direction == "DOWN":
                    move_pct = -move_pct # If we predicted DOWN, a price drop is a win
                
                results.append(move_pct)
            except Exception as e:
                continue

        if not results:
            return {"error": "Simulation yielded no results. Data fetch might have failed."}

        win_rate = (len([r for r in results if r > 0]) / len(results)) * 100
        avg_return = np.mean(results)
        
        return {
            "win_rate": round(win_rate, 2),
            "avg_return_pct": round(float(avg_return), 2),
            "trial_count": len(results),
            "holding_days": holding_days
        }

    def compare_vs_moopredict(self) -> Dict:
        """Compare actual prediction outcomes vs random monkey baseline."""
        db = SessionLocal()
        try:
            # 1. Get watchlist symbols
            watchlist = [s.symbol for s in db.query(UserWatchlist).all()]
            if not watchlist:
                return {"error": "Watchlist is empty. Cannot run comparison."}

            # 2. Run monkey simulation
            monkey_stats = self.run_simulation(watchlist)
            if "error" in monkey_stats:
                return monkey_stats

            # 3. Get MooPredict actual stats from DB
            predictions = db.query(Prediction).filter(Prediction.outcome.isnot(None)).all()
            if not predictions:
                return {
                    "monkey_stats": monkey_stats,
                    "moopredict_stats": "No resolved predictions yet.",
                    "verdict": "Wait for more data."
                }

            correct = [p for p in predictions if p.outcome == "RIGHT"]
            mp_win_rate = (len(correct) / len(predictions)) * 100
            mp_avg_return = np.mean([p.actual_move_pct for p in predictions if p.actual_move_pct is not None])
            
            alpha = mp_win_rate - monkey_stats["win_rate"]
            verdict = "MooPredict is BEATING the monkey! 🧠 > 🐒" if alpha > 0 else "The monkey is currently winning. 🐒 > 🧠"
            
            return {
                "monkey_stats": monkey_stats,
                "moopredict_stats": {
                    "win_rate": round(mp_win_rate, 2),
                    "avg_return_pct": round(float(mp_avg_return), 2),
                    "prediction_count": len(predictions)
                },
                "alpha_pct": round(alpha, 2),
                "verdict": verdict
            }
        finally:
            db.close()

monkey_baseline = MonkeyBaseline()
