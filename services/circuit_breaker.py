import time
from enum import Enum
from typing import Callable, Any, Dict, Optional
from loguru import logger

class CircuitState(Enum):
    CLOSED = "CLOSED"     # Normal operation
    OPEN = "OPEN"         # Failing, returning fallbacks
    HALF_OPEN = "HALF_OPEN" # Testing if service recovered

class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, cooldown_sec: int = 300):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0
        self.last_success_time = 0
        self._cache: Dict[str, Any] = {}

    def call(self, func: Callable, *args, fallback: Any = None, cache_key: Optional[str] = None, **kwargs) -> Any:
        """Execute a function with circuit breaker protection."""
        now = time.time()

        # 1. Check if circuit is OPEN
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time > self.cooldown_sec:
                logger.info(f"[CircuitBreaker:{self.name}] Cooldown expired. Moving to HALF_OPEN.")
                self.state = CircuitState.HALF_OPEN
            else:
                # Return cached value or fallback
                if cache_key and cache_key in self._cache:
                    logger.debug(f"[CircuitBreaker:{self.name}] Circuit OPEN. Returning cached value for {cache_key}.")
                    return self._cache[cache_key]
                logger.warning(f"[CircuitBreaker:{self.name}] Circuit OPEN. No cache. Returning fallback.")
                return fallback

        # 2. Execute call
        try:
            result = func(*args, **kwargs)
            
            # Handle Moomoo style errors (ret != RET_OK) if applicable
            # (Note: This generic breaker assumes success if no exception is raised, 
            # but we can customize it if needed)
            
            self._on_success(cache_key, result)
            return result
            
        except Exception as e:
            return self._on_failure(e, cache_key, fallback)

    def _on_success(self, cache_key: Optional[str], result: Any):
        self.failures = 0
        self.last_success_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            logger.info(f"[CircuitBreaker:{self.name}] Service recovered. Closing circuit.")
            self.state = CircuitState.CLOSED
        
        if cache_key:
            self._cache[cache_key] = result

    def _on_failure(self, error: Exception, cache_key: Optional[str], fallback: Any) -> Any:
        self.failures += 1
        self.last_failure_time = time.time()
        logger.error(f"[CircuitBreaker:{self.name}] Call failed ({self.failures}/{self.failure_threshold}): {error}")

        if self.failures >= self.failure_threshold:
            logger.error(f"[CircuitBreaker:{self.name}] Failure threshold reached. OPENING CIRCUIT.")
            self.state = CircuitState.OPEN
            
        if cache_key and cache_key in self._cache:
            logger.warning(f"[CircuitBreaker:{self.name}] Returning cached value after failure.")
            return self._cache[cache_key]
            
        return fallback

# Global registry of breakers
breakers: Dict[str, CircuitBreaker] = {
    "moomoo": CircuitBreaker("moomoo", failure_threshold=3, cooldown_sec=600),
    "yfinance": CircuitBreaker("yfinance", failure_threshold=2, cooldown_sec=3600), # yfinance is more rate-limited
    "news": CircuitBreaker("news", failure_threshold=3, cooldown_sec=1800)
}

def get_breaker(name: str) -> CircuitBreaker:
    if name not in breakers:
        breakers[name] = CircuitBreaker(name)
    return breakers[name]
