import os
from loguru import logger
from typing import Dict, List, Optional
from services._legacy.options_engine import options_engine

class UOAService:
    def detect_gamma_squeeze(self, symbol: str) -> Dict:
        """Analyze free options flow for gamma squeeze signatures using enhanced options engine."""
        try:
            data = options_engine.get_chain_data(symbol)
            if not data:
                return {"success": False, "error": "Could not fetch options data"}
            
            whales = data.get("whale_trades", [])
            call_whales = [w for w in whales if w['type'] == 'CALL']
            put_whales = [w for w in whales if w['type'] == 'PUT']
            
            risk = "LOW"
            if len(call_whales) >= 2 and data['pcr'] < 0.6:
                risk = "HIGH"
            elif len(call_whales) >= 1:
                risk = "MODERATE"
                
            return {
                "success": True,
                "squeeze_risk": risk,
                "whale_count": len(whales),
                "call_aggression": len(call_whales),
                "put_aggression": len(put_whales),
                "gamma_wall": data['gamma_wall'],
                "support_wall": data['support_wall'],
                "global_pcr": data['pcr']
            }
        except Exception as e:
            logger.error(f"[UOA] Error analyzing {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def get_summary(self, symbol: str) -> Dict:
        """Unified UOA summary using free data."""
        return self.detect_gamma_squeeze(symbol)

uoa_service = UOAService()
