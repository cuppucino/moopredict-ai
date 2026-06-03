# MooPredict Task Tracker

## Active Tasks

### MCPT Re-validation (In Progress)
- **Status**: Running
- **Started**: 2026-05-31
- **Scope**: All 5 strategies × 6 tickers = 30 combinations
- **Strategies**: RSI, VWAP, reversal, volume_profile, signal_aggregator
- **Tickers**: MARA, CVX, SPY, QQQ, AAPL, NVDA
- **Fidelity**: MCPT_PERMS_IS=1000, MCPT_PERMS_WF=200
- **Expected Runtime**: 1-3 hours (parallelized)

## Permanently Deferred Tasks

### Cost Calibration
- **Status**: PERMANENTLY DEFERRED
- **Script**: `scripts/calibrate_costs.py` (kept in repo for reference)
- **Reason**: Only 2 fills available (fractional NVDA + AAPL, both market orders) — insufficient for calibration
- **Final Decision**: ROUND_TRIP_BPS = 0.0020 (20 bps) locked as permanent conservative default
- **Notes**:
  - Conservative assumption ensures we don't overestimate edge
  - If strategies show edge net of 20 bps, it's real
  - Script remains available if fill data accumulates in future

## Completed Tasks

### NVDA Ticker Addition
- **Status**: DONE
- **Date**: 2026-05-31
- **Change**: Added NVDA to mcpt_nightly.py ticker list
- **File**: `services/mcpt_nightly.py` line 115

### Cost Model Unification
- **Status**: DONE
- **Date**: 2026-05-27
- **Change**: Unified ROUND_TRIP_BPS = 0.0020 across all MCPT engines
- **File**: `services/mcpt/costs.py`

### MARA Aggregator Validation
- **Status**: DONE
- **Date**: 2026-05-27
- **Result**: DISABLED (p=0.6550, PF=0.9106) - no edge detected