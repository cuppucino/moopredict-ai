import uuid
import time
from typing import Dict, Optional
from loguru import logger
from services.moomoo_service import moomoo_service

class TreasurerService:
    def __init__(self):
        self.hard_threshold = 50.0  # USD
        self.portfolio_pct_threshold = 10.0 # 10%
        self.auto_approve_max = 20.0 # USD
        self.pending_approvals: Dict[str, Dict] = {}
        self.token_expiry_seconds = 600 # 10 minutes

    def request_approval(self, symbol: str, side: str, qty: float, price: float, is_day_trade: bool = False) -> Dict:
        """Evaluate if a trade needs treasurer approval."""
        total_cost = qty * price
        
        # 1. Check auto-approve floor
        if total_cost <= self.auto_approve_max:
            return {"auto_approved": True}

        # 2. Check dynamic threshold
        balance = moomoo_service.get_balance()
        if balance:
            dynamic_threshold = balance["total_assets"] * (self.portfolio_pct_threshold / 100.0)
            threshold = min(self.hard_threshold, dynamic_threshold)
        else:
            threshold = self.hard_threshold

        if total_cost < threshold:
            return {"auto_approved": True}

        # 3. Requires approval
        token = str(uuid.uuid4())[:6].upper()
        self.pending_approvals[token] = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "total": total_cost,
            "is_day_trade": is_day_trade,
            "expires_at": time.time() + self.token_expiry_seconds
        }
        
        return {
            "auto_approved": False,
            "token": token,
            "total": total_cost,
            "threshold": threshold
        }

    def get_approval(self, token: str) -> Optional[Dict]:
        """Validate and retrieve a pending approval."""
        approval = self.pending_approvals.get(token)
        if not approval:
            return None
        
        if time.time() > approval["expires_at"]:
            del self.pending_approvals[token]
            return None
            
        return approval

    def consume_approval(self, token: str):
        """Remove approval after use."""
        if token in self.pending_approvals:
            del self.pending_approvals[token]

    def cleanup(self):
        """Remove expired tokens."""
        now = time.time()
        expired = [k for k, v in self.pending_approvals.items() if now > v["expires_at"]]
        for k in expired:
            del self.pending_approvals[k]

treasurer_service = TreasurerService()
