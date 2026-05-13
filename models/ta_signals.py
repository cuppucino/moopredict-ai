from dataclasses import dataclass
from typing import List, Optional

@dataclass
class TASignal:
    name: str           # "RSI", "MACD", etc.
    value: float        # Raw indicator value
    signal: str         # "BULLISH" | "BEARISH" | "NEUTRAL"
    strength: int       # -15 to +15 contribution to composite
    detail: str         # Human-readable explanation

@dataclass
class CompositeScore:
    score: int          # 0-100
    label: str          # "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL"
    signals: List[TASignal]
    regime: str         # "TRENDING" | "RANGE_BOUND" | "HIGH_VOLATILITY" | "LOW_VOLATILITY"
