from typing import Dict
from loguru import logger
from services.moomoo_service import moomoo_service

class RiskService:
    def calculate_position_size(self, symbol: str, price: float, risk_percent: float = 10.0) -> Dict:
        """
        Calculate recommended share quantity based on account value and risk rules.
        Rule: Max 30% of total assets per position.
        Rule: Risk is based on a -10% stop loss.
        """
        try:
            balance = moomoo_service.get_balance()
            if not balance:
                return {"error": "Could not fetch account balance"}
            
            total_assets = balance["total_assets"]
            
            # 1. Max allocation rule (30% of total assets)
            max_allocation_usd = total_assets * 0.30
            max_shares_by_allocation = max_allocation_usd / price
            
            # 2. Risk-based sizing
            # Formula: (Account * Risk%) / (Price - StopLoss)
            # User uses: (Account * Risk%) / (Price * 0.10) since StopLoss is Price * 0.90
            max_risk_usd = total_assets * (risk_percent / 100.0)
            stop_loss_price = price * 0.90
            loss_per_share = price - stop_loss_price
            
            if loss_per_share <= 0:
                return {"error": "Invalid loss calculation"}
                
            shares_by_risk = max_risk_usd / loss_per_share
            
            # Final recommendation: conservative minimum of the two
            recommended_shares = min(shares_by_risk, max_shares_by_allocation)
            
            return {
                "symbol": symbol,
                "current_price": price,
                "total_assets": total_assets,
                "risk_percent": risk_percent,
                "stop_loss_price": round(stop_loss_price, 2),
                "recommended_shares": int(recommended_shares),
                "total_cost": round(int(recommended_shares) * price, 2),
                "allocation_percent": round((int(recommended_shares) * price / total_assets) * 100, 2)
            }
            
        except Exception as e:
            logger.error(f"[Risk] Calculation error: {e}")
            return {"error": str(e)}

risk_service = RiskService()
