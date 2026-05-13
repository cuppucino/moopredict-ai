from typing import Dict, Optional
from services.options_engine import options_engine

class OptionsFlowService:
    def get_pcr(self, symbol: str) -> Dict:
        """Compatibility wrapper for the new OptionsEngine."""
        res = options_engine.get_chain_data(symbol)
        if not res:
            return {"error": f"Could not fetch options for {symbol}", "pcr": 1.0}
        return res

options_service = OptionsFlowService()
