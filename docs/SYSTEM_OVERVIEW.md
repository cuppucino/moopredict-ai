# MooPredict AI — System Overview
> Last updated: 2026-05-19 | Version: 1.1

---

## What This System Is

MooPredict AI is a fully autonomous paper trading engine that:
- Monitors US stock markets every 30 minutes during trading hours
- Makes its own buy/sell/hold decisions using a cloud AI model (GLM-5.1)
- Learns from every trade it makes and remembers those lessons permanently
- Accepts trading knowledge from video transcripts you paste via Telegram
- Sends you a morning report every day at 7:00 AM Malaysia time

You sleep. It trades. You wake up to a report.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         YOU (Malaysia)                               │
│                              │                                       │
│              Telegram Bot (! or / commands)                         │
│    !learn  !status  !rules  !lessons  !pause  !resume  !report      │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      FASTAPI BACKEND (main.py)                       │
│                      Running via PM2, always on                      │
│                                                                      │
│   /api/learn    /api/trading/pause    /api/trading/resume            │
│   /api/trading/status    /api/report/now    /api/decisions           │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        CORE SCHEDULER                                │
│                   APScheduler — runs 24/7                            │
│                                                                      │
│  08:30 PM MY  Pre-market sweep (news + Twitter scan)                │
│  09:30 PM MY  Market opens → Decision Loop starts                   │
│  Every 30min  Decision Cycle (9:30 PM – 4:00 AM)                   │
│  Every 5 min  Position Monitor (stop loss / take profit)            │
│  04:30 AM MY  Post-market lesson writing                            │
│  07:00 AM MY  Morning Report → Telegram                             │
└──────────────┬──────────────────────────────────────────────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
┌─────────────┐   ┌──────────────────────────────────────────────┐
│ DATA LAYER  │   │             DECISION ENGINE                   │
│             │   │                                               │
│ News DB     │   │  1. Gather Context (news + Twitter + ports)  │
│ Twitter/X   │   │  2. Load Memory (rules + lessons)            │
│ Heartbeat   │   │  3. Call glm-5.1:cloud                       │
│ Sectors     │   │  4. Parse JSON decision                      │
└──────┬──────┘   │  5. Execute trade or skip                    │
       │           │  6. Log reasoning to DB                      │
       └───────────┴──────────────────┬───────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         POSTGRESQL DATABASE                          │
│                                                                      │
│  paper_trades       all simulated trades with full reasoning         │
│  trading_knowledge  rules learned from your transcripts              │
│  decision_log       every decision made (OPEN/CLOSE/HOLD/SKIP)      │
│  pattern_database   lessons auto-written after each trade            │
│  daily_performance  daily P&L for calendar/report history            │
│  system_state       pause/resume flag and other config               │
└──────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      OLLAMA (localhost:11434)                         │
│                                                                      │
│  glm-5.1:cloud    trade decisions, lessons, rule extraction          │
│  llama3.2:1b      news and Twitter summaries (fast, local)           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Decision Cycle Flow (every 30 minutes, market hours only)

```
SCHEDULER FIRES (every 30 min, 13:30–20:00 UTC)
        │
        ▼
┌───────────────────┐
│  Market hours?    │── NO ──► STOP
│  Mon–Fri          │
│  13:30–20:00 UTC  │
└────────┬──────────┘
         │ YES
         ▼
┌───────────────────┐
│  System paused?   │── YES ──► STOP
└────────┬──────────┘
         │ NO
         ▼
┌─────────────────────────────────┐
│  GATHER CONTEXT                 │
│                                 │
│  • News headlines (last 2hr)    │
│  • Twitter/X posts (last 2hr)   │
│  • Current open positions       │
│  • Available capital balance    │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  LOAD MEMORY                    │
│                                 │
│  • Top 15 trading rules    ◄────┼── trading_knowledge table
│  • Last 10 trade lessons   ◄────┼── pattern_database table
│  • Current win/loss streak      │
└────────────────┬────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────┐
│  CALL glm-5.1:cloud                                  │
│                                                      │
│  Prompt includes:                                    │
│  - Capital available                                 │
│  - Recent news + Twitter                             │
│  - Open positions                                    │
│  - Trading rules (from !learn)                       │
│  - Past lessons (from closed trades)                 │
│                                                      │
│  Returns JSON:                                       │
│  {                                                   │
│    "action": "OPEN | CLOSE | HOLD | SKIP",           │
│    "symbol": "NVDA",                                 │
│    "side": "BUY | SHORT",                            │
│    "confidence": 85,                                 │
│    "catalyst": "Earnings beat + volume surge",       │
│    "reasoning": "Full explanation...",               │
│    "position_size_pct": 30,                          │
│    "close_trade_id": null                            │
│  }                                                   │
└────────────────┬─────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  VALIDATE JSON                  │
│  Missing fields? ───────────────┼──► Log error → SKIP
└────────────────┬────────────────┘
                 │
         ┌───────┼───────┬──────────┐
         ▼       ▼       ▼          ▼
       OPEN    CLOSE    HOLD      SKIP
         │       │       │          │
         ▼       ▼       └──────────┘
    Check 3   Close            │
    position  trade        Log reason
    limit     in DB        to DB
         │       │
         ▼       ▼
    Open      Trigger
    trade     LESSON
    in DB     WRITER
         │
         ▼
    LOG to decision_log
    (every action saved
     with full reasoning)
```

---

## Learning Loop Flow

```
TRADE OPENS
      │
      ▼
  Saved to paper_trades:
  - symbol, side, entry_price
  - stop_loss (-5%), take_profit (+8%)
  - reasoning    (GLM's full explanation)
  - catalyst     (one-line reason)
  - news_context (headlines at entry time)
  - decision_id  (links to decision_log)
      │
      │  (market moves...)
      │
      ▼
STOP LOSS or TAKE PROFIT HIT
(checked every 5 min by position monitor)
      │
      ▼
  Trade closed:
  - exit_price, pnl_amount, pnl_percent
  - outcome: WIN / LOSS / BREAKEVEN
      │
      ▼
┌────────────────────────────────────────────────────┐
│              LESSON WRITER TRIGGERED                │
│                                                    │
│  Prompt sent to glm-5.1:cloud:                     │
│  "This trade WON/LOST.                             │
│   Entry reasoning: [...]                           │
│   Outcome: [P&L%]                                  │
│   Extract one actionable rule."                    │
│                                                    │
│  Returns:                                          │
│  {                                                 │
│    "lesson": "Don't buy without volume spike",     │
│    "category": "ENTRY",                            │
│    "applies_to": "ALL",                            │
│    "confidence": 80                                │
│  }                                                 │
└──────────────────────┬─────────────────────────────┘
                       │
                       ▼
  Saved to pattern_database:
  - confirmed = TRUE  (automatic, no manual step)
  - source = 'auto_lesson'
                       │
                       ▼
  Next decision cycle loads this lesson
  → GLM avoids the same mistake next time
```

---

## Knowledge Ingestion Flow (!learn command)

```
YOU send in Telegram:
  !learn [paste full transcript or article]
        │
        ▼
  bridge-logic.js forwards to /webhook
  main.py routes to knowledge_service
        │
        ▼
┌──────────────────────────────────────────────────┐
│  CHUNK TEXT                                      │
│  Split into 3000-word segments                   │
│  200-word overlap between chunks                 │
└───────────────────────┬──────────────────────────┘
                        │
                        ▼  (for each chunk)
┌──────────────────────────────────────────────────┐
│  CALL glm-5.1:cloud                              │
│                                                  │
│  "Extract ONLY actionable trading rules.         │
│   Ignore stories, ads, opinions.                 │
│   Return as JSON list."                          │
│                                                  │
│  Returns:                                        │
│  { "rules": [                                    │
│    { "rule": "Don't trade in first 15 min",      │
│      "category": "TIMING",                       │
│      "importance": 8 }                           │
│  ]}                                              │
└───────────────────────┬──────────────────────────┘
                        │
                        ▼
  DEDUPLICATE
  Fuzzy match vs existing rules (85% threshold)
  Skip if near-identical rule already exists
                        │
                        ▼
  SAVE to trading_knowledge:
  - source = 'transcript'
  - active = TRUE
                        │
                        ▼
  Telegram reply:
  "✅ Learned 7 new rules:
   1. Don't trade in first 15 min after open
   2. Volume must confirm breakout
   ..."
```

---

## Morning Report Flow (7:00 AM Malaysia)

```
SCHEDULER fires at 23:00 UTC (7:00 AM Malaysia)
        │
        ▼
  Query paper_trades closed in last session
  Query pattern_database lessons (last 24hr)
  Query decision_log (count by action type)
  Calculate: session P&L, win rate, balance
        │
        ▼
  Save row to daily_performance table
        │
        ▼
  Send to Telegram:

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📊 MooPredict AI Daily Report
  Date: 2026-05-20

  📈 Session Performance
  • P&L: +$3.30
  • Win Rate: 60.0% (3W / 2L)

  💰 Account Status
  • Balance: $225.10
  • Total Return: +1.6%

  🤖 AI Summary
  [GLM 2-sentence session summary]
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Scheduler Timeline

| Malaysia Time | UTC       | Job ID | What runs |
|---------------|-----------|--------|-----------|
| 8:30 PM       | 12:30 UTC | `trading_loop_premarket` | Scrape news + Twitter before open |
| 9:30 PM       | 13:30 UTC | `trading_loop_open` | Market opens, first decision cycle |
| Every :00/:30 | 14–19 UTC | `trading_loop_mid` | Decision cycle every 30 min |
| 4:00 AM       | 20:00 UTC | `trading_loop_close` | Final decision cycle at market close |
| 4:30 AM       | 20:30 UTC | `trading_loop_postmarket` | Batch lesson writing for all closed trades |
| 7:00 AM       | 23:00 UTC | `morning_report_job` | Send daily report to Telegram |
| Every 5 min   | 13–21 UTC | `paper_stop_check_job` | Check stop loss / take profit on open trades |
| Every 30 min  | All day   | `heartbeat_check_job` | System health, price + sector updates |
| Every 15 min  | All day   | `x_scrape_job` | Scrape Twitter/X |
| Every hour    | All day   | `news_scrape_job` | Scrape news headlines |

---

## Telegram Commands

| Command | What it does |
|---------|-------------|
| `!learn [text]` | Paste any transcript or article. GLM extracts rules and saves them permanently into the knowledge base. |
| `!status` | Shows open positions, live P&L, capital balance, and whether autonomous loop is active or paused. |
| `!rules` | Lists all trading rules currently in the knowledge base (from transcripts + auto-learned). |
| `!lessons` | Lists the last 10 lessons the system wrote from its own closed trades. |
| `!pause` | Stops the loop from opening new trades. Monitoring and stop-loss checks continue. |
| `!resume` | Resumes autonomous trading. |
| `!report` | Generates and sends the full report right now without waiting for 7 AM. |

All commands also work with `/` prefix (e.g. `/learn`, `/status`).

---

## Files Added in v1.1

| File | Purpose |
|------|---------|
| `services/decision_engine.py` | Core brain. Gathers context, loads memory, calls GLM, routes OPEN/CLOSE/HOLD/SKIP. |
| `services/trading_loop.py` | Orchestrator. Market hours gate, pause flag, max 3 positions. Calls decision engine. |
| `services/lesson_writer.py` | Triggered when trade closes. Calls GLM to analyze outcome, writes lesson to DB. |
| `services/knowledge_service.py` | Handles `!learn`. Chunks text, extracts rules via GLM, deduplicates, saves to DB. |
| `services/report_service.py` | Builds the morning Telegram report, saves daily performance record. |

---

## Database Tables Added in v1.1

| Table | What it stores |
|-------|---------------|
| `trading_knowledge` | Rules from `!learn` transcripts. Fed into every GLM prompt. |
| `decision_log` | Every decision made (OPEN/CLOSE/HOLD/SKIP) with full reasoning and errors. |
| `daily_performance` | Daily P&L snapshot. One row per trading day. Used for history/calendar. |
| `system_state` | Key-value config. Currently stores `trading_paused = true/false`. |

**Columns added to existing tables:**

| Table | New Columns |
|-------|------------|
| `paper_trades` | `reasoning`, `catalyst`, `decision_id`, `news_context` |
| `pattern_database` | `source` — values: `auto_lesson`, `transcript`, `manual` |

---

## API Routes Added in v1.1

| Method | Route | Purpose |
|--------|-------|---------|
| `POST` | `/api/learn` | Body: `{"text": "..."}` — ingest transcript |
| `POST` | `/api/trading/pause` | Pause autonomous loop |
| `POST` | `/api/trading/resume` | Resume autonomous loop |
| `GET`  | `/api/trading/status` | Current loop state + open position count |
| `POST` | `/api/report/now` | Generate + return full report immediately |
| `GET`  | `/api/decisions` | Recent decision_log entries |

---

## Risk Rules

| Rule | Setting |
|------|---------|
| Max open positions at any time | 3 |
| Position size per trade | 20–50% of available capital (GLM decides) |
| Stop loss — BUY | −5% from entry price |
| Take profit — BUY | +8% from entry price |
| Stop loss — SHORT | +5% from entry price |
| Take profit — SHORT | −8% from entry price |
| Drawdown warning | Logged when session loss exceeds 30% of starting capital |
| Losing streak | GLM is told the streak count and becomes more selective naturally |
| Hard stop | None — system keeps trading and learning |

---

## Model Routing

| Task | Model | Reason |
|------|-------|--------|
| Trade decisions | `glm-5.1:cloud` | Strong reasoning needed over news + context |
| Post-trade lesson analysis | `glm-5.1:cloud` | Needs to understand outcome vs reasoning |
| Transcript rule extraction | `glm-5.1:cloud` | Complex filtering of long, noisy text |
| News/Twitter summaries | `llama3.2:1b` | Fast, local, good enough for summaries |

---

## How "Learning" Actually Works

The system does not retrain any model. GLM's weights are fixed by the provider.

What improves over time is the **context that gets fed into GLM before every decision**:

1. Rules you teach it via `!learn` (grow as you paste more transcripts)
2. Lessons it writes itself from every closed trade (grow as more trades happen)

As these two stores grow, GLM has more relevant history to reason from, and decisions improve. Think of it as a trader building a notebook of rules — the notebook gets better with every entry.

---

## Starting Baseline

| Metric | Value |
|--------|-------|
| Starting capital (simulated) | $221.65 USD |
| Mode | Paper trading only — no real account access |
| Target before going live | Consistent win rate 50%+ shown in weekly reports |

---

## Known Limitations

| Limitation | Detail |
|-----------|--------|
| Requires Mac to be on | No cloud server. System only runs while your Mac is running. |
| No real-time price feed | Prices fetched from Moomoo OpenD at decision time. If disconnected, trade is skipped. |
| `!learn` processing time | Very long transcripts may take a few minutes to process (chunked). |
| US stocks only | GLM decides which US stocks to trade. No options, crypto, or other markets yet. |
| Paper trading only | All trades are fully simulated until you decide to enable real account access. |
