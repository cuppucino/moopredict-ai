# TODOS

## From /autoplan review of 11_snapback_plan.md (2026-07-30)

### 1. Validate the W_NEWS bet against realized outcomes (P1, next research priority)
- **What:** Backtest/calibrate the focus engine's news-signal weight (W_NEWS 0.16) against resolved focus predictions and historical outcomes.
- **Why:** The 2026-07-28 ledger concluded the info layer is "the only path to genuine >54% skill" — it is the differentiated, load-bearing bet of the whole architecture, and it is UNTESTED while technical signals keep getting research budget.
- **Pros:** Validates (or kills) the system's core thesis; directly improves the primary [FOCUS] track.
- **Cons:** Needs enough resolved focus predictions for signal; news-attribution methodology is fiddly.
- **Context:** services/focus_engine.py combines per-ETF priors + W_NEWS*news + W_TECH*tech. News signal = sentiment_engine + driver-keyword 48h matching. Start by replaying stored news signals against resolved outcomes (predictions table, category="focus").
- **Effort:** M (human) → S with CC. **Priority:** P1. **Depends on:** growing focus n (already accumulating daily).

### 2. Spec and gate the PCR-extreme contrarian conditioner (P2)
- **What:** Proper mini-plan for put/call-ratio extremes as a contrarian confidence conditioner: thresholds, sample-size estimate, its own backtest + gate.
- **Why:** PCR data is already collected and unused; kf's source lists put/call contrarian as a valid family. Was Step 3 of the snapback plan; removed because it was an unspecified hand-wave.
- **Pros:** Cheap data already flowing; complements the info layer.
- **Cons:** Extremes are rare → small n; needs its own validation to avoid another zombie signal.
- **Context:** Follow the snapback validation template (regime-stratified history + corrected MCPT). Wire only as a conditioner on focus priors, not a standalone tag, unless evidence says otherwise.
- **Effort:** M → S with CC. **Priority:** P2. **Depends on:** snapback Step 1 harness (reuses it).

### 3. Widen [SNAPBACK] universe beyond the focus 4 (P3, checkpoint-gated)
- **What:** If the snapback signal survives validation AND the live exit-rule checkpoint (≥15 non-NEUTRAL events without retirement), evaluate widening from SPY/QQQ/SMH/XLE to the 10-ETF backtest universe.
- **Why:** Clustered triggers make 4-ETF live n grow slowly; the backtest evidence covers 10 ETFs.
- **Pros:** Faster evidence accumulation. **Cons:** More clustered (correlated) events, not proportionally more information.
- **Context:** scratch/openclaw_research_loop/11_snapback_plan.md Step 2 trigger spec; exit rule in the same file.
- **Effort:** S → S. **Priority:** P3. **Depends on:** snapback live checkpoint.

### 4. Generalize the validation harness (P3)
- **What:** When a second candidate signal appears, refactor scripts/backtest_snapback_history.py + scripts/mcpt_snapback.py into a signal-agnostic validation harness (regime-stratified history + joint-permutation MCPT, pluggable trigger/hold definitions).
- **Why:** The validate → gate → monitor → retire process is the durable asset; every future signal should pay the same toll.
- **Pros:** One hardened harness, consistent evidence standards. **Cons:** Premature before a second signal exists — hence deferred.
- **Context:** The bar_permute traps (seed-per-iteration, index alignment, column normalization) are already handled in the snapback scripts; lift them, don't rediscover them.
- **Effort:** M → S with CC. **Priority:** P3. **Depends on:** a second candidate signal.
