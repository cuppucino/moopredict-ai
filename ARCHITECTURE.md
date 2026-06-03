# MooPredict — System Architecture

> Last updated: 2026-05-28 — focused on the current state after the MCPT cost-unification work.
> For older narrative context (Telegram bot, decision-loop history), see [docs/SYSTEM_OVERVIEW.md](docs/SYSTEM_OVERVIEW.md).

---

## 30-Second Mental Model

MooPredict has **four layers**. Most confusion comes from conflating them.

```
┌──────────────────────────────────────────────────────────────────┐
│  L4  EXECUTION       — actually buys/sells via Moomoo            │
│      (decision_engine → moomoo_service → broker)                  │
├──────────────────────────────────────────────────────────────────┤
│  L3  GATING          — decides which strategies are allowed live  │
│      (mcpt_nightly.py applies LIVE / WATCHLIST / DISABLED)        │
├──────────────────────────────────────────────────────────────────┤
│  L2  VALIDATION      — proves a strategy has a real edge          │
│      (mcpt/validator.py + mcpt/walkforward.py — permutation tests)│
├──────────────────────────────────────────────────────────────────┤
│  L1  SIGNAL          — raw indicators on price data               │
│      (mcpt/adapters/*.py — RSI, VWAP, reversal, volume profile)   │
└──────────────────────────────────────────────────────────────────┘

Plus two cross-cutting services:
  ▸ DATA    — OHLC (yfinance, Moomoo), positions, news, sentiment
  ▸ NOTIFY  — alerts to Telegram / dashboard
```

A signal at L1 must survive L2 validation before L3 can mark it LIVE; only then will L4 trade it. Nothing trades that isn't LIVE.

---

## File Map (the parts that matter)

### Entry points

| File | What it does |
|---|---|
| [main.py](main.py) | FastAPI server. Starts scheduler, exposes REST API. |
| [core/scheduler.py](core/scheduler.py) | APScheduler cron — ~20 jobs (scrapers, briefings, MCPT nightly, trading loop). |
| [services/mcpt_nightly.py](services/mcpt_nightly.py) | The MCPT pipeline runner. Cron entry point for L2 + L3. |
| [services/trading_loop.py](services/trading_loop.py) | Market-hours decision cycle. Calls `decision_engine`. |

### Layer 1 — Signal generation

| File | Signal |
|---|---|
| [services/mcpt/adapters/rsi_adapter.py](services/mcpt/adapters/rsi_adapter.py) | RSI oversold/overbought |
| [services/mcpt/adapters/vwap_adapter.py](services/mcpt/adapters/vwap_adapter.py) | VWAP mean-reversion |
| [services/mcpt/adapters/reversal_adapter.py](services/mcpt/adapters/reversal_adapter.py) | Reversal pattern |
| [services/mcpt/adapters/volume_profile_adapter.py](services/mcpt/adapters/volume_profile_adapter.py) | Volume cluster |
| [services/mcpt/adapters/signal_aggregator_adapter.py](services/mcpt/adapters/signal_aggregator_adapter.py) | Consensus of the above 4 |

Adapters return `+1 / 0 / -1` per bar. They do **not** compute returns — that's centralized in `validator.py` / `walkforward.py` to avoid lookahead bias.

### Layer 2 — MCPT validation

| File | Role |
|---|---|
| [services/mcpt/optimizer.py](services/mcpt/optimizer.py) | Grid-search best params per adapter |
| [services/mcpt/validator.py](services/mcpt/validator.py) | In-sample permutation test → `insample_p` |
| [services/mcpt/walkforward.py](services/mcpt/walkforward.py) | Rolling out-of-sample test → `wf_p` |
| [services/mcpt/costs.py](services/mcpt/costs.py) | **Single source of truth** for transaction costs (`ROUND_TRIP_BPS`, `PER_FLIP_BPS`) |
| [services/mcpt/profit_factor.py](services/mcpt/profit_factor.py) | PF = gross profit / gross loss |
| [scripts/calibrate_costs.py](scripts/calibrate_costs.py) | Empirically calibrate `ROUND_TRIP_BPS` from Moomoo fills (**not yet run**) |

### Layer 3 — Gating

| File | Role |
|---|---|
| [services/mcpt_nightly.py](services/mcpt_nightly.py) | Runs nightly, applies pre-registered gating rules, writes to `MCPTResult` table |

**Gating rules:**

| `insample_p` | `wf_p` | Status |
|---|---|---|
| < 0.01 | < 0.05 | LIVE (after EMA hysteresis: EMA_p < 0.04) |
| One passes | One fails | WATCHLIST |
| Both fail | Both fail | DISABLED |

Bootstrap: first 5 runs forced to WATCHLIST/DISABLED. LIVE→non-LIVE requires EMA_p > 0.08.

### Layer 4 — Execution

| File | Role |
|---|---|
| [services/decision_engine.py](services/decision_engine.py) | Combines MCPT status + signal consensus + risk → trade decision |
| [services/moomoo_service.py](services/moomoo_service.py) | Futu OpenD SDK wrapper — connect, place_order, history |
| [services/trading_loop.py](services/trading_loop.py) | Market-hours loop — calls decision_engine, respects `SystemState.paused` |
| [services/risk_engine.py](services/risk_engine.py) | Position sizing, VAR checks (**Kelly sizing not yet wired**) |

### Data layer

| File | Role |
|---|---|
| [core/database.py](core/database.py) | SQLAlchemy models on PostgreSQL |
| [data/heartbeat_state.json](data/heartbeat_state.json) | In-memory state snapshot for monitoring |

**Key tables:**
- `MCPTResult` — strategy validation history (the L3 output)
- `TradeJournal` — every executed trade
- `SystemState` — global pause flag
- `UserWatchlist` — symbols to monitor
- `Prediction`, `NewsIntel`, `PatternDB`, `Earnings`, `SocialPost`, `PriceAlert`

### Cross-cutting

| File | Role |
|---|---|
| [services/notifications.py](services/notifications.py) | In-memory queue → polled by Telegram bot / dashboard |
| [services/ai_service.py](services/ai_service.py) | Local Ollama (Llama 3.2:1b) for summarization |
| [services/sentiment_engine.py](services/sentiment_engine.py) | Sentiment scoring per symbol |
| [services/ml_engine.py](services/ml_engine.py) | LSTM + Random Forest forecasts (weekly retrain) |

---

## The MCPT Pipeline — End-to-End Flow

This is the core thing to understand. It runs nightly per (strategy, ticker) pair.

```
┌─────────────────────────────────────────────────────────────────┐
│ STEP 1 — Load 8y of daily OHLC from yfinance                    │
│          (mcpt_nightly.py)                                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ STEP 2 — Optimize strategy params (grid search)                 │
│          (mcpt/optimizer.py) → best_params                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ STEP 3 — In-sample MCPT (1000 permutations)                     │
│          Compare real PF vs. shuffled-signal PFs                 │
│          (mcpt/validator.py) → insample_p                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ STEP 4 — Walk-forward MCPT (200 permutations)                   │
│          Rolling 1500-bar train / 100-bar step                   │
│          (mcpt/walkforward.py) → wf_p, real_pf                   │
│          Costs deducted via mcpt/costs.py (flip-magnitude scaled)│
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ STEP 5 — Apply gating rules + 5-day EMA hysteresis              │
│          (mcpt_nightly.update_mcpt_status)                       │
│          → LIVE / WATCHLIST / DISABLED                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ STEP 6 — Persist to MCPTResult table + alert on status change   │
│          (notification_queue)                                    │
└─────────────────────────────────────────────────────────────────┘
```

**Cost model (post-fix):**
- `ROUND_TRIP_BPS = 0.0020` (20 bps round-trip, conservative default — still pending empirical calibration)
- `PER_FLIP_BPS = 0.0010` (10 bps per one-way trade)
- Flip cost is **proportional to position-change magnitude** — `-1 → +1` costs 2× a `0 → +1`.

---

## Scheduler Jobs (UTC)

| Job | Schedule | Layer |
|---|---|---|
| News / X / Reddit scrapers | hourly / 15m / 30m | DATA |
| Morning briefing | 02:00 | NOTIFY |
| Pre-market prep | 13:00 | DATA |
| Market open alerts | 13:30 Mon–Fri | NOTIFY |
| TA / sentiment snapshots | hourly during market | DATA |
| Trailing stops | every 5m during market | L4 |
| Trading decision cycle | during market hours | L4 |
| Heartbeat | every 15m during market | monitoring |
| Daily P&L | 21:00 Mon–Fri | NOTIFY |
| **MCPT nightly validation** | nightly | **L2+L3** |
| Weekly ML retrain | Sun 05:00 | ML |
| Weekly pattern review | Sun 06:00 | learning |

---

## Current State (as of 2026-05-28)

### What's working
- ✅ MCPT validation pipeline end-to-end (just fixed and re-verified)
- ✅ Transaction-cost model unified across optimizer / validator / walkforward
- ✅ Signal-timing audit confirms no lookahead bias ([validator.py:25,56,165,207](services/mcpt/validator.py#L25))
- ✅ 21 tests covering cost scaling, Moomoo history parsing, indicators

### What's currently DISABLED (i.e. not trading)
| Strategy | Ticker | Status | Why |
|---|---|---|---|
| signal_aggregator | MARA | DISABLED | wf_p = 0.6550, PF = 0.91 — no statistical edge under realistic costs |
| signal_aggregator | CVX, SPY, QQQ, AAPL | DISABLED | Warmup (need ≥ 5 runs before any LIVE status possible) |
| RSI / VWAP / reversal / volume profile standalone | all | Unknown | Not yet re-validated under new cost model |

**Net: no strategies are currently LIVE. The system is conservative-by-design until validation produces a real edge.**

### What's pending
- 🟡 Run [scripts/calibrate_costs.py](scripts/calibrate_costs.py) against 90d of real Moomoo fills to replace the 20-bps placeholder
- 🟡 Re-validate RSI / VWAP / reversal / volume profile at empirical cost
- 🟡 Sharpe-ratio tracking (after an edge is found)
- 🟡 Fractional Kelly position sizing (after an edge is found)
- 🟡 One sentiment signal tested rigorously (Put/Call ratio candidate)

### Deferred (will not be built soon)
- HMM regime detection (needs 10y data, not 8y)
- COT institutional flow data
- RL agent (overfitting risk too high)

---

## How a New Strategy Gets to LIVE

1. Write an adapter in `services/mcpt/adapters/` returning `+1 / 0 / -1` signals.
2. Add it to `mcpt_nightly.py`'s ticker × strategy loop.
3. Wait 5 nightly runs (bootstrap freeze → WATCHLIST/DISABLED only).
4. After run 5: if `insample_p < 0.01` AND `wf_p < 0.05` AND `EMA_p < 0.04`, promotes to **LIVE**.
5. While LIVE, `decision_engine` will size and place trades; `trading_loop` executes during market hours.
6. If `EMA_p > 0.08`, demotes back to WATCHLIST. Hysteresis prevents flapping.

---

## Common Confusions, Resolved

**"Why is everything DISABLED if we have a working pipeline?"**
Because the strategies we have don't yet show a statistical edge under realistic costs. That's the gate working — better to trade nothing than to trade noise.

**"What's the difference between `backtest_engine.py` and the MCPT pipeline?"**
`backtest_engine.py` (vectorbt-based) is a quick simulation for exploration / dashboards. The MCPT pipeline is the **statistical proof** that a strategy isn't curve-fit. Only MCPT results gate live trading.

**"Why doesn't anything use Anthropic/Claude API?"**
The AI layer uses local Ollama (Llama 3.2:1b) for summarization of news/social posts. No LLM is in the trading decision path — decisions are deterministic from MCPT status + signal consensus + risk rules.

**"Where do I look first when something breaks?"**
1. `data/heartbeat_state.json` — current system snapshot
2. `logs/` — last 10 days of structured logs (loguru)
3. `MCPTResult` table — strategy gating history
4. `TradeJournal` table — what actually executed

---

## Glossary

| Term | Meaning |
|---|---|
| **MCPT** | Monte Carlo Permutation Test — shuffles signals to compute a p-value for "is this edge real or random?" |
| **PF** | Profit Factor = gross winning $ / gross losing $. > 1 = profitable. |
| **wf_p** | Walk-forward p-value — out-of-sample significance |
| **insample_p** | In-sample p-value — significance on training data |
| **EMA_p** | 5-day exponential moving average of `wf_p`, used for hysteresis |
| **bps** | Basis points. 100 bps = 1%. 20 bps = 0.20%. |
| **Flip** | A position change. `0→+1` is one flip; `-1→+1` is two flips' worth of cost. |
| **LIVE / WATCHLIST / DISABLED** | The three MCPT gating states. Only LIVE strategies can trade. |
