import time
from typing import Dict, Optional
from loguru import logger
from datetime import datetime

class DataFreshnessRegistry:
    def __init__(self):
        self._registry: Dict[str, float] = {} # { "source:symbol": timestamp }
        self._global_registry: Dict[str, float] = {} # { "source": timestamp }

    def register_update(self, source: str, symbol: Optional[str] = None):
        """Register a successful data update for a source and optionally a symbol."""
        now = time.time()
        if symbol:
            key = f"{source}:{symbol.upper()}"
            self._registry[key] = now
        
        self._global_registry[source] = now
        logger.debug(f"[Freshness] Registered update for {source}{' (symbol:' + symbol + ')' if symbol else ''}")

    def get_age_minutes(self, source: str, symbol: Optional[str] = None) -> float:
        """Get the age of data in minutes."""
        now = time.time()
        last_update = None
        
        if symbol:
            last_update = self._registry.get(f"{source}:{symbol.upper()}")
        
        if last_update is None:
            last_update = self._global_registry.get(source)
            
        if last_update is None:
            return 999999.0 # Effectively infinite age if never updated
            
        return (now - last_update) / 60

    def is_fresh(self, source: str, max_age_min: float, symbol: Optional[str] = None) -> bool:
        """Check if data is within the freshness threshold."""
        return self.get_age_minutes(source, symbol) <= max_age_min

    def get_staleness_report(self) -> Dict[str, float]:
        """Return a report of all sources and their age in minutes."""
        report = {}
        for source, last_time in self._global_registry.items():
            report[source] = round((time.time() - last_time) / 60, 1)
        return report

    def stamp_prediction_context(self, symbol: str) -> Dict:
        """Generate a freshness snapshot for a specific symbol's prediction context."""
        symbol = symbol.upper()
        return {
            "price_age_min": round(self.get_age_minutes("moomoo_quote", symbol), 1),
            "sentiment_age_min": round(self.get_age_minutes("sentiment_engine", symbol), 1),
            "ta_age_min": round(self.get_age_minutes("ta_engine", symbol), 1),
            "options_age_min": round(self.get_age_minutes("options_engine", symbol), 1),
            "timestamp_utc": datetime.utcnow().isoformat()
        }

# Singleton instance
freshness_registry = DataFreshnessRegistry()
