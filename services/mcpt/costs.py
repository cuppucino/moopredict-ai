"""
MCPT transaction cost model settings.
Single source of truth for transaction fees/costs.
"""

ROUND_TRIP_BPS = 0.0020  # 20 bps; override per-asset via dict if needed
PER_FLIP_BPS = ROUND_TRIP_BPS / 2  # 10 bps per one-way trade
