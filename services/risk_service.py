import pytz
from datetime import datetime
from typing import Dict, List
from loguru import logger
from sqlalchemy.orm import Session
from core.database import SessionLocal, DrawdownTracker, TradeJournal, UserWatchlist
from services.moomoo_service import moomoo_service

class RiskService:
    def __init__(self):
        self.max_drawdown_pct = 10.0
        self.base_risk_pct = 2.0  # Default 2%
        self.large_portfolio_threshold = 500.0
        self.max_allocation_pct = 15.0

    def get_current_risk_params(self) -> Dict:
        """Calculate dynamic risk parameters based on portfolio state."""
        db = SessionLocal()
        try:
            balance = moomoo_service.get_balance()
            if not balance:
                # Return empty/default if balance can't be fetched
                return {
                    "risk_pct": 2.0,
                    "blocked": False,
                    "reason": "⚠️ Could not fetch real-time balance. Using default risk.",
                    "total_assets": 0.0,
                    "streak": self._get_loss_streak(db),
                    "at_risk_usd": 0.0,
                    "heat_pct": 0.0,
                    "drawdown_pct": 0.0,
                    "market_status": "NEUTRAL"
                }
            
            total_assets = balance["total_assets"]
            
            # 1. Base risk based on portfolio size
            current_base_risk = 2.0 if total_assets < self.large_portfolio_threshold else 1.0
            
            # 2. Check loss streak
            streak = self._get_loss_streak(db)
            adjusted_risk = current_base_risk
            streak_warning = ""
            
            if streak >= 5:
                return {
                    "risk_pct": 0, 
                    "blocked": True, 
                    "reason": "🔴 REVENGE TRADE PROTECT: 5+ consecutive losses. Trading BLOCKED.",
                    "total_assets": total_assets,
                    "streak": streak,
                    "drawdown_pct": 0.0 # Will be updated below
                }
            elif streak >= 3:
                adjusted_risk = current_base_risk * 0.5
                streak_warning = f"⚠️ Risk halved to {adjusted_risk}% after {streak} losses."
            elif streak >= 2:
                adjusted_risk = current_base_risk * 0.75
                streak_warning = f"⚠️ Risk reduced to {adjusted_risk}% after {streak} losses."

            # 3. Check Drawdown
            dd_status = self.check_drawdown(db, total_assets)
            
            # 4. Market Status (Optional: could pull from SPY sentiment)
            market_status = "BULL" # Default to BULL for now, or could integrate sentiment_service.get_sentiment_score("SPY")
            
            # 5. Portfolio Heat (Total risk % of account)
            # For a single trade, it's just the adjusted_risk. 
            # In a multi-position account, it's the sum of risk on all open positions.
            # Simplified for now: just the current allowed risk per trade.
            at_risk_usd = total_assets * (adjusted_risk / 100.0)
            heat_pct = (at_risk_usd / total_assets * 100.0) if total_assets > 0 else 0.0

            result = {
                "risk_pct": adjusted_risk,
                "blocked": dd_status["is_blocked"],
                "reason": streak_warning or ("Normal risk parameters." if not dd_status["is_blocked"] else dd_status["blocked_reason"]),
                "total_assets": total_assets,
                "streak": streak,
                "at_risk_usd": at_risk_usd,
                "heat_pct": heat_pct,
                "drawdown_pct": dd_status["drawdown_pct"],
                "market_status": market_status
            }
            
            if dd_status["is_blocked"]:
                result["blocked"] = True
                result["reason"] = f"🔴 MAX DRAWDOWN REACHED ({dd_status['drawdown_pct']:.2f}%)"
            
            return result
            
        finally:
            db.close()

    def calculate_position_size(self, symbol: str, price: float, custom_risk: float = None) -> Dict:
        """
        Calculate recommended share quantity.
        Rules: 
        - 2% risk (or 1% if >$500)
        - Max 15% allocation
        - Adjusted for loss streaks
        """
        try:
            risk_params = self.get_current_risk_params()
            if risk_params.get("blocked"):
                return {"error": risk_params["reason"]}
            
            total_assets = risk_params["total_assets"]
            risk_pct = custom_risk if custom_risk is not None else risk_params["risk_pct"]
            
            if total_assets <= 0:
                return {"error": "Portfolio value is zero. Cannot calculate position size."}

            # 1. Max allocation rule (15% of total assets)
            max_allocation_usd = total_assets * (self.max_allocation_pct / 100.0)
            max_shares_by_allocation = max_allocation_usd / price
            
            # 2. Risk-based sizing
            is_leveraged = any(x in symbol.upper() for x in ["2X", "3X", "SMCX", "ARMG", "LABU", "TQQQ", "SOXL"])
            is_option = len(symbol) > 10 or (symbol.count('.') >= 2)
            
            # Default stop loss percentages per asset class
            if is_leveraged:
                stop_pct = 0.03 # -3%
            elif is_option:
                stop_pct = 0.10 # -10%
            else:
                stop_pct = 0.05 # -5%
            
            risk_amount_usd = total_assets * (risk_pct / 100.0)
            shares_by_risk = risk_amount_usd / (price * stop_pct)
            
            # Final recommendation: conservative minimum of the two
            recommended_shares = min(shares_by_risk, max_shares_by_allocation)
            
            # Round down to nearest whole share
            recommended_shares = int(recommended_shares)
            
            if recommended_shares <= 0:
                # If even 1 share exceeds risk, we return a warning
                return {"error": f"Risk threshold too low for 1 share of {symbol} at ${price}."}

            return {
                "symbol": symbol,
                "current_price": price,
                "total_assets": round(total_assets, 2),
                "risk_pct": risk_pct,
                "is_leveraged": is_leveraged,
                "is_option": is_option,
                "stop_loss_price": round(price * (1 - stop_pct), 2),
                "recommended_shares": recommended_shares,
                "total_cost": round(recommended_shares * price, 2),
                "allocation_percent": round((recommended_shares * price / total_assets) * 100, 2),
                "reason": risk_params["reason"]
            }
            
        except Exception as e:
            logger.error(f"[Risk] Calculation error: {e}")
            return {"error": str(e)}

    def check_drawdown(self, db: Session, current_value: float) -> Dict:
        """Track portfolio peak and block trading if drawdown exceeds 10%."""
        tracker = db.query(DrawdownTracker).first()
        
        if not tracker:
            tracker = DrawdownTracker(peak_value=current_value, current_value=current_value)
            db.add(tracker)
            db.commit()
            return {"is_blocked": False, "drawdown_pct": 0.0, "peak_value": current_value}

        # Update peak if current is higher
        if current_value > tracker.peak_value:
            tracker.peak_value = current_value
        
        tracker.current_value = current_value
        drawdown = 0.0
        if tracker.peak_value > 0:
            drawdown = ((tracker.peak_value - current_value) / tracker.peak_value) * 100
            
        tracker.drawdown_pct = drawdown
        
        if drawdown >= self.max_drawdown_pct:
            tracker.is_blocked = True
            tracker.blocked_reason = f"Max Drawdown of {drawdown:.2f}% reached."
        
        db.commit()
        return {
            "is_blocked": tracker.is_blocked,
            "drawdown_pct": drawdown,
            "peak": tracker.peak_value,
            "blocked_reason": tracker.blocked_reason
        }

    def _get_loss_streak(self, db: Session) -> int:
        """Calculate current consecutive loss streak."""
        trades = db.query(TradeJournal).filter(TradeJournal.status == "CLOSED").order_by(TradeJournal.exit_time.desc()).limit(10).all()
        
        streak = 0
        for t in trades:
            if t.outcome == "LOSS":
                streak += 1
            else:
                break
        return streak

    def reset_drawdown(self):
        """Manually reset the circuit breaker."""
        db = SessionLocal()
        try:
            tracker = db.query(DrawdownTracker).first()
            if tracker:
                balance = moomoo_service.get_balance()
                current_value = balance["total_assets"] if balance else tracker.current_value
                tracker.peak_value = current_value
                tracker.is_blocked = False
                tracker.drawdown_pct = 0.0
                db.commit()
            return True
        finally:
            db.close()

risk_service = RiskService()
