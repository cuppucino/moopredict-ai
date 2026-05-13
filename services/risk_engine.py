import math
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger
from core.database import SessionLocal, TradeJournal, Prediction
from services.moomoo_service import moomoo_service

class RiskEngine:
    """
    Advanced Risk Models:
    - Value at Risk (Historical VaR)
    - Expected Shortfall (CVaR)
    - Kelly Criterion (Optimal Position Sizing)
    - Sharpe Ratio
    - Portfolio Heat Map
    """

    # ─── Value at Risk (VaR) ──────────────────────────────────────────────

    def calculate_var(self, symbol: str, confidence: float = 0.95, days: int = 1, lookback: int = 252) -> Dict:
        """
        Historical VaR using yfinance daily returns.
        confidence: 0.95 = 95% VaR (the loss you won't exceed 95% of the time)
        days: holding period
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=f"{lookback}d")

            if hist.empty or len(hist) < 30:
                return {"error": f"Insufficient data for {symbol} (need 30+ days)"}

            # Daily log returns
            returns = np.log(hist['Close'] / hist['Close'].shift(1)).dropna()

            # Historical VaR = percentile of the return distribution
            var_pct = np.percentile(returns, (1 - confidence) * 100)

            # Scale to multi-day holding period (sqrt-of-time rule)
            var_pct_scaled = var_pct * math.sqrt(days)

            # Dollar VaR (per $1 invested)
            current_price = float(hist['Close'].iloc[-1])

            # CVaR (Expected Shortfall) = average of losses beyond VaR
            tail_returns = returns[returns <= var_pct]
            cvar_pct = float(tail_returns.mean()) if len(tail_returns) > 0 else var_pct

            return {
                "symbol": symbol,
                "current_price": round(current_price, 2),
                "confidence": confidence,
                "holding_days": days,
                "lookback_days": lookback,
                "var_pct": round(var_pct_scaled * 100, 2),       # e.g., -2.5%
                "var_usd_per_share": round(current_price * abs(var_pct_scaled), 2),
                "cvar_pct": round(cvar_pct * 100, 2),            # Expected Shortfall
                "daily_volatility": round(float(returns.std()) * 100, 2),
                "annualized_volatility": round(float(returns.std()) * math.sqrt(252) * 100, 2),
            }
        except Exception as e:
            logger.error(f"[RiskEngine] VaR error for {symbol}: {e}")
            return {"error": str(e)}

    # ─── Kelly Criterion ──────────────────────────────────────────────────

    def calculate_kelly(self, symbol: str = None) -> Dict:
        """
        Kelly Criterion: f* = (bp - q) / b
        where:
            b = avg win / avg loss (win/loss ratio)
            p = probability of winning
            q = 1 - p (probability of losing)
        
        Uses your actual trade history for accuracy.
        If symbol is provided, calculates Kelly for that symbol only.
        """
        db = SessionLocal()
        try:
            query = db.query(TradeJournal).filter(
                TradeJournal.status == "CLOSED",
                TradeJournal.outcome.in_(["WIN", "LOSS"])
            )
            if symbol:
                query = query.filter(TradeJournal.symbol == symbol.upper())

            trades = query.all()

            if len(trades) < 5:
                # Fall back to prediction history
                return self._kelly_from_predictions(db, symbol)

            wins = [t for t in trades if t.outcome == "WIN"]
            losses = [t for t in trades if t.outcome == "LOSS"]

            if not wins or not losses:
                return {"kelly_pct": 0, "label": "INSUFFICIENT_DATA", "reason": "Need both wins and losses."}

            p = len(wins) / len(trades)
            q = 1 - p

            avg_win = abs(np.mean([t.pnl_percent for t in wins if t.pnl_percent]))
            avg_loss = abs(np.mean([t.pnl_percent for t in losses if t.pnl_percent]))

            if avg_loss == 0:
                return {"kelly_pct": 0, "label": "ZERO_LOSS", "reason": "Average loss is zero. Cannot compute."}

            b = avg_win / avg_loss  # Win/Loss ratio
            kelly_full = (b * p - q) / b

            # Half-Kelly is safer (industry standard)
            kelly_half = kelly_full / 2
            kelly_quarter = kelly_full / 4

            label = "AGGRESSIVE" if kelly_full > 0.25 else "MODERATE" if kelly_full > 0.10 else "CONSERVATIVE" if kelly_full > 0 else "NO_EDGE"

            return {
                "symbol": symbol or "PORTFOLIO",
                "total_trades": len(trades),
                "win_rate": round(p * 100, 1),
                "avg_win_pct": round(avg_win, 2),
                "avg_loss_pct": round(avg_loss, 2),
                "win_loss_ratio": round(b, 2),
                "kelly_full_pct": round(kelly_full * 100, 2),
                "kelly_half_pct": round(kelly_half * 100, 2),   # Recommended
                "kelly_quarter_pct": round(kelly_quarter * 100, 2),
                "label": label,
                "recommendation": f"Risk {round(kelly_half * 100, 1)}% of portfolio per trade (Half-Kelly)."
            }
        except Exception as e:
            logger.error(f"[RiskEngine] Kelly error: {e}")
            return {"error": str(e)}
        finally:
            db.close()

    def _kelly_from_predictions(self, db, symbol: str = None) -> Dict:
        """Fallback: compute Kelly from prediction accuracy if trade data is sparse."""
        query = db.query(Prediction).filter(Prediction.outcome.in_(["RIGHT", "WRONG"]))
        if symbol:
            query = query.filter(Prediction.symbol == symbol.upper())

        preds = query.all()
        if len(preds) < 5:
            return {
                "kelly_pct": 2.0,
                "label": "DEFAULT",
                "reason": f"Only {len(preds)} data points. Using default 2% risk.",
                "data_source": "predictions"
            }

        rights = [p for p in preds if p.outcome == "RIGHT"]
        wrongs = [p for p in preds if p.outcome == "WRONG"]

        p = len(rights) / len(preds)
        q = 1 - p

        avg_win = abs(np.mean([p.actual_move_pct for p in rights if p.actual_move_pct])) if rights else 1.0
        avg_loss = abs(np.mean([p.actual_move_pct for p in wrongs if p.actual_move_pct])) if wrongs else 1.0

        b = avg_win / avg_loss if avg_loss > 0 else 1.0
        kelly_full = (b * p - q) / b if b > 0 else 0
        kelly_half = kelly_full / 2

        return {
            "symbol": symbol or "PORTFOLIO",
            "total_predictions": len(preds),
            "accuracy": round(p * 100, 1),
            "kelly_full_pct": round(kelly_full * 100, 2),
            "kelly_half_pct": round(kelly_half * 100, 2),
            "label": "PREDICTION_BASED",
            "recommendation": f"Risk {round(max(kelly_half * 100, 0.5), 1)}% per trade (from prediction history).",
            "data_source": "predictions"
        }

    # ─── Sharpe Ratio ─────────────────────────────────────────────────────

    def calculate_sharpe(self, symbol: str, risk_free_rate: float = 0.05, lookback: int = 252) -> Dict:
        """
        Annualized Sharpe Ratio = (mean_return - risk_free) / std_return * sqrt(252)
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=f"{lookback}d")

            if hist.empty or len(hist) < 30:
                return {"error": f"Insufficient data for {symbol}"}

            returns = hist['Close'].pct_change().dropna()
            daily_rf = risk_free_rate / 252

            excess_returns = returns - daily_rf
            sharpe = float(excess_returns.mean() / excess_returns.std() * math.sqrt(252))

            label = "EXCELLENT" if sharpe > 2.0 else "GOOD" if sharpe > 1.0 else "AVERAGE" if sharpe > 0.5 else "POOR"

            return {
                "symbol": symbol,
                "sharpe_ratio": round(sharpe, 2),
                "label": label,
                "annualized_return": round(float(returns.mean()) * 252 * 100, 2),
                "annualized_volatility": round(float(returns.std()) * math.sqrt(252) * 100, 2),
                "risk_free_rate": risk_free_rate
            }
        except Exception as e:
            logger.error(f"[RiskEngine] Sharpe error for {symbol}: {e}")
            return {"error": str(e)}

    # ─── Smart Position Sizing (Kelly-Adjusted) ───────────────────────────

    def smart_position_size(self, symbol: str, price: float) -> Dict:
        """
        Combines Kelly Criterion with VaR to produce an
        intelligent position size recommendation.
        """
        try:
            balance = moomoo_service.get_balance()
            if not balance:
                return {"error": "Could not fetch portfolio balance."}

            total_assets = balance["total_assets"]
            if total_assets <= 0:
                return {"error": "Portfolio value is zero."}

            # Get Kelly
            kelly = self.calculate_kelly(symbol)
            kelly_pct = kelly.get("kelly_half_pct", 2.0)
            # Clamp Kelly between 0.5% and 10%
            kelly_pct = max(0.5, min(10.0, kelly_pct))

            # Get VaR
            var = self.calculate_var(symbol, confidence=0.95, days=1)
            var_pct = abs(var.get("var_pct", 2.5))

            # Kelly tells us HOW MUCH to risk, VaR tells us the STOP distance
            risk_usd = total_assets * (kelly_pct / 100.0)
            stop_distance_pct = max(var_pct / 100.0, 0.01)  # At least 1%
            shares = int(risk_usd / (price * stop_distance_pct))

            if shares <= 0:
                shares = 1

            total_cost = shares * price
            allocation_pct = (total_cost / total_assets) * 100

            return {
                "symbol": symbol,
                "current_price": round(price, 2),
                "total_assets": round(total_assets, 2),
                "kelly_risk_pct": round(kelly_pct, 2),
                "var_stop_pct": round(var_pct, 2),
                "recommended_shares": shares,
                "total_cost": round(total_cost, 2),
                "allocation_pct": round(allocation_pct, 2),
                "stop_loss_price": round(price * (1 - stop_distance_pct), 2),
                "risk_usd": round(risk_usd, 2),
                "method": "Kelly + VaR Hybrid"
            }
        except Exception as e:
            logger.error(f"[RiskEngine] Smart sizing error: {e}")
            return {"error": str(e)}

risk_engine = RiskEngine()
