================================================================================
OPENCLAW TRADE JOURNAL & PATTERN RECOGNITION — IMPLEMENTATION PLAN
================================================================================
Created: 2026-04-29
Status:  PENDING APPROVAL
================================================================================

GOAL
────
Give OpenClaw a "Memory" — the ability to:
  • Automatically log every trade with entry thesis, outcome, and lessons
  • Build a living pattern database of market observations over time
  • Learn from past trades to improve future decision-making
  • Export trade history and patterns for review

================================================================================
CURRENT STATE (What Already Exists)
================================================================================

  ✅ manual_positions table  — Records symbol, entry/exit price, status
  ❌ No thesis tracking      — No "WHY" behind each trade
  ❌ No lesson capture        — No post-mortem or reflection
  ❌ No pattern database      — No reusable market observations
  ❌ No auto-logging          — !buy/!sell doesn't record to a journal
  ❌ No trade stats           — No win rate, avg P&L, streak tracking

================================================================================
WHAT TO BUILD (2 Database Tables + 2 Services + Webhook Commands)
================================================================================

──────────────────────────────────────────────────────────────────────────────
TABLE 1: trade_journal (New Database Table)
──────────────────────────────────────────────────────────────────────────────

File: core/database.py (modify)

Columns:
  id              INTEGER     Primary key, auto-increment
  symbol          TEXT        e.g. "UEC", "AAPL"
  side            TEXT        "BUY" or "SELL"
  quantity        FLOAT       Number of shares
  entry_price     FLOAT       Price at entry
  exit_price      FLOAT       Price at exit (NULL if still open)
  order_type      TEXT        "MARKET" or "LIMIT"
  status          TEXT        "OPEN" | "CLOSED" | "STOPPED_OUT"
  thesis          TEXT        WHY this trade was made (the reasoning)
  stop_loss       FLOAT       Stop loss price (if set)
  take_profit     FLOAT       Target price (if set)
  tags            TEXT        Comma-separated labels, e.g. "uranium,momentum,swing"
  outcome         TEXT        "WIN" | "LOSS" | "BREAKEVEN" | NULL (if open)
  pnl_amount      FLOAT       Dollar profit/loss
  pnl_percent     FLOAT       Percentage profit/loss
  lessons         TEXT        Post-trade reflection / lessons learned
  entry_time      DATETIME    When the trade was opened
  exit_time       DATETIME    When the trade was closed (NULL if open)
  created_at      DATETIME    Row creation timestamp

Key Design Decisions:
  • Separate from manual_positions — that table is for raw position tracking.
    trade_journal is the "thinking layer" on top: WHY, WHAT LEARNED.
  • thesis and lessons are free-text fields so OpenClaw can write
    rich narratives, not just numbers.
  • tags allow categorization for later pattern analysis
    (e.g., "show me all 'momentum' trades").

──────────────────────────────────────────────────────────────────────────────
TABLE 2: pattern_database (New Database Table)
──────────────────────────────────────────────────────────────────────────────

File: core/database.py (modify)

Columns:
  id              INTEGER     Primary key, auto-increment
  name            TEXT        Short name, e.g. "Energy vs Uranium Divergence"
  category        TEXT        "DIVERGENCE" | "BREAKOUT" | "REVERSAL" | "CORRELATION" | "MACRO" | "OTHER"
  observation     TEXT        What was observed in the market
  thesis          TEXT        Why this might be significant
  symbols         TEXT        Comma-separated related symbols, e.g. "XLE,URA,UEC"
  data_snapshot   TEXT        Supporting data (prices, ratios, etc.)
  confirmed       BOOLEAN     Was this pattern confirmed by subsequent action?
  confirmation    TEXT        How/when it was confirmed (or invalidated)
  lesson          TEXT        What we learned from this pattern
  times_seen      INTEGER     How many times this pattern has appeared (default 1)
  last_seen       DATETIME    Most recent occurrence
  created_at      DATETIME    First recorded

Key Design Decisions:
  • category field enables filtering ("show me all DIVERGENCE patterns")
  • times_seen + last_seen track recurrence — the more a pattern repeats,
    the more valuable it becomes
  • confirmed flag separates "hypothesis" from "proven pattern"
  • data_snapshot preserves the market context at discovery time

──────────────────────────────────────────────────────────────────────────────
SERVICE 1: Trade Journal Service
──────────────────────────────────────────────────────────────────────────────

File: services/trade_journal.py (NEW)

Purpose:
  Record, update, and analyze trade history. Automatically hooks into
  the !buy/!sell flow so every trade gets logged without extra effort.

Functions:

  log_trade(symbol, side, qty, price, order_type, thesis="", stop=None, target=None, tags="")
    → Creates a new trade_journal entry with status "OPEN"
    → Called automatically when !buy or !sell executes successfully
    → Returns the trade ID for reference

  close_trade(trade_id, exit_price, lessons="")
    → Marks a trade as "CLOSED"
    → Calculates pnl_amount and pnl_percent
    → Sets outcome to "WIN", "LOSS", or "BREAKEVEN"
    → Saves the lessons learned

  update_thesis(trade_id, thesis)
    → Update the reasoning behind an existing trade
    → OpenClaw can refine her thesis as new info arrives

  add_lesson(trade_id, lesson)
    → Append a lesson to an existing trade
    → Can be added at any time (even months later)

  set_stops(trade_id, stop_loss, take_profit)
    → Update stop loss and take profit levels

  get_open_trades()
    → Returns all trades with status "OPEN"

  get_trade_history(limit=20)
    → Returns recent closed trades

  get_trade_stats()
    → Calculates aggregate statistics:
      • Total trades
      • Win rate (% of winning trades)
      • Average win size vs average loss size
      • Largest win / largest loss
      • Current streak (consecutive wins or losses)
      • Best performing tag/category
      • Profit factor (gross wins / gross losses)

  search_trades(symbol=None, tag=None, outcome=None)
    → Filter trade history by criteria

Auto-Logging Hook:
  When !buy or !sell succeeds in main.py, automatically call:
    trade_journal.log_trade(symbol, side, qty, price, order_type)
  This ensures EVERY trade is recorded even if OpenClaw forgets.
  She can then add the thesis and lessons separately.

──────────────────────────────────────────────────────────────────────────────
SERVICE 2: Pattern Database Service
──────────────────────────────────────────────────────────────────────────────

File: services/pattern_service.py (NEW)

Purpose:
  Store and retrieve market patterns that OpenClaw discovers.
  Over time, this becomes her institutional knowledge.

Functions:

  record_pattern(name, category, observation, thesis, symbols="", data="")
    → Creates a new pattern entry
    → OpenClaw calls this when she spots something interesting
    → Returns the pattern ID

  confirm_pattern(pattern_id, confirmation_text)
    → Marks a pattern as confirmed
    → Records how/when it played out

  invalidate_pattern(pattern_id, reason)
    → Marks a pattern as NOT confirmed
    → Records why it didn't work

  bump_pattern(pattern_id)
    → Increments times_seen and updates last_seen
    → Called when an existing pattern appears again

  get_patterns(category=None, confirmed=None)
    → Returns patterns, optionally filtered by category or confirmation

  get_pattern_summary()
    → AI-powered: summarizes the most reliable patterns
    → "Your most confirmed pattern is X, seen Y times"

  search_patterns(query)
    → Full-text search across pattern names, observations, and theses

──────────────────────────────────────────────────────────────────────────────
WEBHOOK COMMANDS (New)
──────────────────────────────────────────────────────────────────────────────

  Trade Journal Commands:
  ─────────────────────
  !tradelog                          → View recent trades (last 10)
  !tradelog open                     → View all open trades
  !tradelog stats                    → Win rate, avg P&L, streaks
  !thesis TRADE_ID "reason text"     → Add/update thesis for a trade
  !lesson TRADE_ID "what I learned"  → Add lesson to a trade
  !stop TRADE_ID 13.50 18.00         → Set stop loss and take profit
  !close TRADE_ID 15.20              → Manually close a trade at a price

  Pattern Database Commands:
  ─────────────────────────
  !pattern "Energy Divergence" DIVERGENCE "XLE up but URA down" "Should correlate" XLE,URA
    → Records a new pattern
  !patterns                          → List recent patterns
  !patterns DIVERGENCE               → Filter by category
  !confirm PATTERN_ID "It played out as expected"
    → Confirm a pattern
  !invalidate PATTERN_ID "Didn't repeat"
    → Mark pattern as invalid

──────────────────────────────────────────────────────────────────────────────
API ENDPOINTS (New)
──────────────────────────────────────────────────────────────────────────────

  GET  /api/trades                   → Recent trade history
  GET  /api/trades/open              → Open trades only
  GET  /api/trades/stats             → Aggregate trade statistics
  POST /api/trades/{id}/thesis       → Update thesis
  POST /api/trades/{id}/lesson       → Add lesson
  POST /api/trades/{id}/close        → Close a trade

  GET  /api/patterns                 → All patterns
  GET  /api/patterns/{category}      → Filtered patterns
  POST /api/patterns                 → Record new pattern
  POST /api/patterns/{id}/confirm    → Confirm a pattern

──────────────────────────────────────────────────────────────────────────────
AUTO-LOGGING INTEGRATION
──────────────────────────────────────────────────────────────────────────────

File: main.py (modify)

The existing !buy and !sell webhook handlers will be updated to
automatically call trade_journal.log_trade() on success:

  BEFORE (current):
    result = moomoo_service.place_order(symbol, qty, side, ...)
    if result["success"]:
        return {"content": "✅ Trade Success"}

  AFTER (upgraded):
    result = moomoo_service.place_order(symbol, qty, side, ...)
    if result["success"]:
        trade_id = trade_journal.log_trade(symbol, side, qty, price, order_type)
        return {"content": f"✅ Trade #{trade_id} logged | {qty} {symbol} ({side})"}

This means OpenClaw never has to manually log a trade.
She just executes !buy or !sell and it's automatically journaled.
She can then add context later with !thesis and !lesson.

──────────────────────────────────────────────────────────────────────────────
SMART FEATURES (AI-Powered)
──────────────────────────────────────────────────────────────────────────────

1. Auto-Thesis Generation:
   When a trade is logged, the system can optionally ask Ollama:
   "Based on recent news and technicals, generate a thesis for
    why {side} {symbol} at ${price} was a good/bad idea."
   This gives OpenClaw a starting point for her reasoning.

2. Pattern Detection:
   During the daily briefing, the system can scan for known patterns
   in the current market data and alert if any match:
   "⚠️ Pattern Alert: 'Energy vs Uranium Divergence' detected again.
    Last seen 3 days ago. Historically bullish for UEC."

3. Trade Review:
   After a trade is closed, the system can ask Ollama:
   "Review this trade: Bought UEC at $14.82, sold at $16.50.
    Thesis was 'uranium momentum'. What lessons should be recorded?"

──────────────────────────────────────────────────────────────────────────────
FILE CHANGES SUMMARY
──────────────────────────────────────────────────────────────────────────────

  NEW FILES:
    services/trade_journal.py         Trade logging, stats, search
    services/pattern_service.py       Pattern recording and retrieval

  MODIFIED FILES:
    core/database.py                  Add TradeJournal + PatternDB tables
    main.py                           Add API endpoints + webhook commands
                                      Hook auto-logging into !buy/!sell

  NO NEW DEPENDENCIES:
    Everything uses existing libraries (SQLAlchemy, Ollama, pandas)

──────────────────────────────────────────────────────────────────────────────
BUILD ORDER
──────────────────────────────────────────────────────────────────────────────

  Phase A — Trade Journal (Core)
    [ ] Add TradeJournal model to core/database.py
    [ ] Create services/trade_journal.py
    [ ] Add /api/trades endpoints to main.py
    [ ] Add !tradelog, !thesis, !lesson, !stop, !close commands
    [ ] Hook auto-logging into !buy/!sell handlers
    [ ] Test: !buy AAPL 1 → verify trade logged → !tradelog

  Phase B — Pattern Database (Core)
    [ ] Add PatternDB model to core/database.py
    [ ] Create services/pattern_service.py
    [ ] Add /api/patterns endpoints to main.py
    [ ] Add !pattern, !patterns, !confirm, !invalidate commands
    [ ] Test: !pattern "Test" DIVERGENCE "obs" "thesis" AAPL

  Phase C — Smart Features (AI Layer)
    [ ] Auto-thesis generation on trade entry
    [ ] Pattern detection in daily briefings
    [ ] Trade review prompt on close
    [ ] Update !help with all new commands

──────────────────────────────────────────────────────────────────────────────
ESTIMATED TIMELINE
──────────────────────────────────────────────────────────────────────────────

  Phase A: ~30 minutes
  Phase B: ~20 minutes
  Phase C: ~15 minutes

  Total: ~1 hour

================================================================================
READY TO START?
================================================================================

Say "go" and I will begin with Phase A (Trade Journal).
Once complete, every trade OpenClaw makes will be permanently recorded
with thesis, stops, lessons, and full performance statistics.

================================================================================
