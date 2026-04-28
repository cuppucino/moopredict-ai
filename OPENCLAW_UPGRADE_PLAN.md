================================================================================
OPENCLAW MARKET INTELLIGENCE UPGRADE — IMPLEMENTATION PLAN
================================================================================
Created: 2026-04-29
Status:  PENDING APPROVAL
================================================================================

GOAL
────
Give OpenClaw full access to market intelligence so she can:
  • Analyze RSI/MACD for entry/exit timing on any stock
  • Pull earnings calendar data for upcoming reports
  • Compare sector ETF performance
  • Detect unusual options activity (Vol/OI ratios)
  • Receive automated daily briefings at scheduled times
  • Deep-dive into current positions with technical analysis

================================================================================
CURRENT STATE (What Already Works)
================================================================================

  ✅ News Scraper        — RSS feeds every 60 min (BBC, Reuters, CNBC, etc.)
  ✅ X Scraper           — Nitter RSS every 15 min (11 accounts)
  ✅ Reddit Scraper      — r/wallstreetbets, r/stocks, etc. every 30 min
  ✅ AI Summarization    — Ollama (llama3.2:1b) summarizes all scraped content
  ✅ Moomoo Connection   — Real account connected, positions/balance/orders
  ✅ API Endpoints       — /api/news, /api/social, /api/positions, /api/balance
  ✅ Telegram Delivery   — Notifications forwarded to Telegram via poller
  ✅ Webhook Commands    — !news, !x, !reddit, !pos, !buy, !sell, etc.

================================================================================
WHAT TO BUILD (6 New Services + Scheduler Upgrades)
================================================================================

──────────────────────────────────────────────────────────────────────────────
SERVICE 1: Technical Analysis Service (🔴 HIGH PRIORITY)
──────────────────────────────────────────────────────────────────────────────

File: services/technical_analysis.py

Purpose:
  Calculate RSI, MACD, Moving Averages, and Bollinger Bands for any stock
  using data from the Moomoo API (which is already connected).

How It Works:
  1. Uses moomoo_service.quote_ctx to fetch K-line (candlestick) data
  2. Calculates indicators using pandas (no extra library needed)
  3. Returns a structured report for OpenClaw to interpret

Data Source:
  • Moomoo OpenD API → quote_ctx.get_cur_kline() or request_history_kline()
  • Must subscribe to stock data first: quote_ctx.subscribe([code], [SubType.K_DAY])

Functions:
  get_rsi(symbol, period=14)
    → Returns: current RSI value, overbought/oversold signal
    → RSI > 70 = overbought, RSI < 30 = oversold

  get_macd(symbol, fast=12, slow=26, signal=9)
    → Returns: MACD line, signal line, histogram, crossover direction
    → Bullish = MACD crosses above signal, Bearish = below

  get_moving_averages(symbol)
    → Returns: SMA20, SMA50, SMA200, current price position relative to each
    → "Price above SMA200 = bullish trend" etc.

  get_bollinger_bands(symbol, period=20, std_dev=2)
    → Returns: upper band, middle band, lower band, current position
    → Near lower band = potential buy, near upper = potential sell

  get_full_analysis(symbol)
    → Combines all above into one comprehensive report
    → OpenClaw can call this with !ta AAPL

RSI Calculation (Pure Pandas):
  delta = df['close'].diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
  rs = gain / loss
  rsi = 100 - (100 / (1 + rs))

MACD Calculation:
  ema12 = df['close'].ewm(span=12, adjust=False).mean()
  ema26 = df['close'].ewm(span=26, adjust=False).mean()
  macd_line = ema12 - ema26
  signal_line = macd_line.ewm(span=9, adjust=False).mean()
  histogram = macd_line - signal_line

API Endpoint:
  GET /api/ta/{symbol}  → Full technical analysis for a stock
  GET /api/ta/{symbol}/rsi  → RSI only

Webhook Command:
  !ta AAPL     → Full technical analysis
  !rsi UEC     → RSI only
  !macd NVDA   → MACD only

Dependencies:
  • pandas (already installed via futu-api dependency)
  • No new pip packages needed

Important Notes:
  • Moomoo requires subscription before fetching K-line data
  • US stocks use prefix "US." (e.g., "US.AAPL")
  • HK stocks use prefix "HK." (e.g., "HK.00700")
  • Must auto-detect and add prefix if missing
  • Rate limit: max 10 subscriptions per 30 seconds

──────────────────────────────────────────────────────────────────────────────
SERVICE 2: Earnings Calendar Service (🔴 HIGH PRIORITY)
──────────────────────────────────────────────────────────────────────────────

File: services/earnings_calendar.py

Purpose:
  Know WHEN companies report earnings, so OpenClaw can warn you before
  volatility events and prepare analysis.

How It Works:
  1. Uses yfinance library to fetch earnings dates for stocks
  2. Scrapes Yahoo Finance earnings calendar for weekly overview
  3. Stores results and serves via API

Data Source:
  Primary:  yfinance library (free, no API key)
    ticker = yf.Ticker("AAPL")
    ticker.earnings_dates  → DataFrame with upcoming/past earnings

  Backup:   Yahoo Finance RSS / web scrape
    https://finance.yahoo.com/calendar/earnings

Functions:
  get_earnings_for_stock(symbol)
    → Returns: next earnings date, EPS estimate, revenue estimate
    → "AAPL reports May 1 after-hours, EPS est: $1.63"

  get_earnings_this_week()
    → Returns: list of all major earnings for current week
    → Grouped by day, sorted by market cap

  get_watchlist_earnings(watchlist)
    → Returns: earnings dates for all stocks in user's watchlist
    → "UEC: May 7, AAPL: May 1, URA: N/A (ETF)"

API Endpoint:
  GET /api/earnings               → This week's earnings calendar
  GET /api/earnings/{symbol}      → Earnings for specific stock
  GET /api/earnings/watchlist     → Earnings for watchlist stocks

Webhook Command:
  !earnings          → This week's major earnings
  !earnings AAPL     → Next earnings for AAPL

Dependencies:
  • yfinance (NEW — add to requirements.txt)

──────────────────────────────────────────────────────────────────────────────
SERVICE 3: Sector ETF Analysis Service (🟡 MEDIUM PRIORITY)
──────────────────────────────────────────────────────────────────────────────

File: services/sector_analysis.py

Purpose:
  Compare sector performance so OpenClaw can identify which sectors are
  strong/weak and where money is flowing.

How It Works:
  1. Uses yfinance to fetch price data for sector ETFs
  2. Calculates 1-day, 1-week, 1-month, 3-month returns
  3. Ranks sectors from strongest to weakest

Sector ETFs to Track:
  XLK  — Technology
  XLF  — Financials
  XLE  — Energy
  XLV  — Health Care
  XLI  — Industrials
  XLY  — Consumer Discretionary
  XLP  — Consumer Staples
  XLU  — Utilities
  XLB  — Materials
  XLRE — Real Estate
  XLC  — Communication Services
  URA  — Uranium (Global X Uranium ETF)
  SPY  — S&P 500 (benchmark)
  QQQ  — Nasdaq 100 (benchmark)

Functions:
  get_sector_performance()
    → Returns: table of all sectors with 1D, 1W, 1M, 3M returns
    → Sorted by 1-day performance (strongest first)

  get_sector_rotation_signal()
    → Compares current vs 1-month rankings to detect rotation
    → "Energy moving from rank #5 to #2 — money flowing in"

  compare_sectors(sector1, sector2)
    → Side-by-side comparison of two sector ETFs

API Endpoint:
  GET /api/sectors              → Full sector heatmap
  GET /api/sectors/{etf}        → Detail for one sector ETF

Webhook Command:
  !sectors           → Sector performance table
  !sector XLE        → Energy sector detail

Dependencies:
  • yfinance (shared with earnings service)

──────────────────────────────────────────────────────────────────────────────
SERVICE 4: Options Flow Scanner (🟢 LOW PRIORITY)
──────────────────────────────────────────────────────────────────────────────

File: services/options_flow.py

Purpose:
  Detect unusual options activity that might signal big-money moves.
  This is the FREE/DIY approach using Volume/Open Interest ratios.

How It Works:
  1. Uses yfinance to fetch the full options chain for a stock
  2. Calculates Volume/Open Interest (Vol/OI) ratio for each contract
  3. Filters for high ratios (> 2.0) = potentially unusual activity
  4. Separates calls vs puts to determine sentiment

Functions:
  scan_options_flow(symbol)
    → Returns: list of unusual contracts (high Vol/OI)
    → "AAPL May 280 Call — Vol: 15,000 / OI: 2,000 = 7.5x (BULLISH)"

  get_put_call_ratio(symbol)
    → Returns: aggregate put/call ratio
    → < 0.7 = bullish sentiment, > 1.0 = bearish/hedging

  scan_watchlist_flow()
    → Scans all watchlist stocks for unusual activity

API Endpoint:
  GET /api/options/{symbol}     → Options flow for a stock
  GET /api/options/pcr/{symbol} → Put/Call ratio

Webhook Command:
  !options AAPL      → Unusual options for AAPL
  !pcr UEC           → Put/Call ratio for UEC

Limitations:
  • NOT real-time (yfinance data is delayed ~15-20 min)
  • Cannot detect sweeps or block trades (need paid service for that)
  • Best used for end-of-day analysis, not intraday decisions

Dependencies:
  • yfinance (shared)

──────────────────────────────────────────────────────────────────────────────
SERVICE 5: Daily Briefing System (🔴 HIGH PRIORITY)
──────────────────────────────────────────────────────────────────────────────

File: services/daily_briefing.py

Purpose:
  Automated daily intelligence reports sent to OpenClaw at scheduled times
  so she can proactively analyze and advise you.

Briefing Schedule (Malaysia Time):
  10:00 AM — Morning Briefing (what happened overnight in US markets)
  04:00 PM — Pre-Market Prep (upcoming earnings, key levels)
  09:30 PM — Market Open Alert (futures, pre-market movers)
  04:00 AM — End-of-Day Summary (what happened today)

Morning Briefing (10:00 AM MYT) Contains:
  1. US market close recap (S&P, Nasdaq, Dow final numbers)
  2. Overnight news summary (top 5 headlines from news_intel DB)
  3. Current positions status (from Moomoo)
  4. Social media sentiment overnight (from social_posts DB)
  5. Key economic events today

Pre-Market Prep (04:00 PM MYT) Contains:
  1. Earnings reporting today/tomorrow
  2. Technical levels for current positions (RSI, support/resistance)
  3. Sector performance snapshot
  4. Updated watchlist status

Market Open Alert (09:30 PM MYT) Contains:
  1. S&P 500 / Nasdaq futures direction
  2. Pre-market movers from watchlist
  3. Key levels to watch for current positions
  4. Any breaking news in last hour

End-of-Day Summary (04:00 AM MYT) Contains:
  1. Portfolio P&L change for the day
  2. Sector winners and losers
  3. New positions / closed positions
  4. Tomorrow's earnings to watch

Functions:
  generate_morning_briefing()
  generate_premarket_briefing()
  generate_market_open_briefing()
  generate_eod_summary()

  Each function:
  1. Aggregates data from all other services
  2. Passes to Ollama for AI-powered summary
  3. Sends formatted message to notification_queue
  4. OpenClaw receives it and can act on it

Scheduler Integration:
  Add 4 new cron jobs to core/scheduler.py:
    "0 2 * * *"   → 10:00 AM MYT (UTC+8) = 02:00 UTC
    "0 8 * * *"   → 04:00 PM MYT = 08:00 UTC
    "30 13 * * *"  → 09:30 PM MYT = 13:30 UTC
    "0 20 * * *"  → 04:00 AM MYT = 20:00 UTC

Dependencies:
  • Uses all other services (technical_analysis, earnings, sectors)
  • Ollama for AI summaries

──────────────────────────────────────────────────────────────────────────────
SERVICE 6: Market Research Helper (🟡 MEDIUM PRIORITY)
──────────────────────────────────────────────────────────────────────────────

File: services/market_research.py

Purpose:
  Give OpenClaw the ability to deep-dive into a stock with one command:
  fundamentals, technicals, news, social sentiment, all in one report.

Functions:
  research_stock(symbol)
    → Combines:
      1. Current price & change (from Moomoo quote_ctx.get_market_snapshot)
      2. RSI/MACD/MAs (from technical_analysis service)
      3. Recent news mentioning this stock (from news_intel DB)
      4. Recent social posts mentioning this stock (from social_posts DB)
      5. Next earnings date (from earnings_calendar service)
      6. Options unusual activity (from options_flow service)
      7. Sector performance context (from sector_analysis)
    → Sends all data to Ollama for a comprehensive AI report
    → Returns structured report

API Endpoint:
  GET /api/research/{symbol}    → Full research report

Webhook Command:
  !research AAPL     → Deep-dive research on AAPL
  !dd UEC            → Same thing (due diligence alias)

================================================================================
NEW WEBHOOK COMMANDS SUMMARY
================================================================================

  Technical Analysis:
    !ta {SYMBOL}           Full technical analysis (RSI + MACD + MAs + BB)
    !rsi {SYMBOL}          RSI only
    !macd {SYMBOL}         MACD only

  Earnings:
    !earnings              This week's earnings calendar
    !earnings {SYMBOL}     Specific stock earnings date

  Sectors:
    !sectors               Sector performance heatmap
    !sector {ETF}          Specific sector detail

  Options:
    !options {SYMBOL}      Unusual options activity
    !pcr {SYMBOL}          Put/Call ratio

  Research:
    !research {SYMBOL}     Full stock deep-dive
    !dd {SYMBOL}           Alias for !research

  Briefings (manual trigger):
    !briefing              Trigger morning briefing now
    !premarket             Trigger pre-market prep now

================================================================================
NEW API ENDPOINTS SUMMARY
================================================================================

  GET  /api/ta/{symbol}             Technical analysis
  GET  /api/ta/{symbol}/rsi         RSI only
  GET  /api/ta/{symbol}/macd        MACD only
  GET  /api/earnings                This week's earnings
  GET  /api/earnings/{symbol}       Stock-specific earnings
  GET  /api/earnings/watchlist      Watchlist earnings
  GET  /api/sectors                 Sector performance
  GET  /api/sectors/{etf}           Specific sector
  GET  /api/options/{symbol}        Options flow
  GET  /api/options/pcr/{symbol}    Put/Call ratio
  GET  /api/research/{symbol}       Full research report

================================================================================
DATABASE CHANGES
================================================================================

No new tables needed. All new services use:
  • news_intel (existing) — for news lookups
  • social_posts (existing) — for social sentiment
  • user_watchlist (existing) — for watchlist-aware features

Technical analysis and options data are computed on-the-fly (not stored).
Earnings data is cached in memory (refreshed daily).

================================================================================
DEPENDENCIES TO ADD
================================================================================

requirements.txt additions:
  yfinance            # Earnings calendar, sector data, options chains
  feedparser          # Already in use but not in requirements.txt
  requests            # Already in use but not in requirements.txt

Install command:
  pip3 install yfinance feedparser requests

================================================================================
FILE CHANGES SUMMARY
================================================================================

  NEW FILES:
    services/technical_analysis.py    RSI, MACD, MAs, Bollinger Bands
    services/earnings_calendar.py     Earnings dates, weekly calendar
    services/sector_analysis.py       Sector ETF comparison
    services/options_flow.py          Vol/OI scanner, Put/Call ratio
    services/daily_briefing.py        4x daily automated briefings
    services/market_research.py       Full stock deep-dive reports

  MODIFIED FILES:
    main.py                           Add new API endpoints + webhook commands
    core/scheduler.py                 Add 4 daily briefing cron jobs
    requirements.txt                  Add yfinance, feedparser, requests

================================================================================
BUILD ORDER
================================================================================

  Phase 1 — Technical Analysis (Day 1)
    [ ] Create services/technical_analysis.py
    [ ] Add Moomoo K-line subscription logic
    [ ] Implement RSI, MACD, MA, Bollinger Band calculations
    [ ] Add /api/ta endpoints to main.py
    [ ] Add !ta, !rsi, !macd webhook commands
    [ ] Test with: curl http://127.0.0.1:3001/api/ta/US.AAPL
    [ ] Test with: !ta AAPL via Telegram

  Phase 2 — Earnings Calendar (Day 1)
    [ ] pip3 install yfinance
    [ ] Create services/earnings_calendar.py
    [ ] Add /api/earnings endpoints to main.py
    [ ] Add !earnings webhook command
    [ ] Test: !earnings AAPL

  Phase 3 — Sector Analysis (Day 2)
    [ ] Create services/sector_analysis.py
    [ ] Add /api/sectors endpoints to main.py
    [ ] Add !sectors webhook command
    [ ] Test: !sectors

  Phase 4 — Daily Briefings (Day 2)
    [ ] Create services/daily_briefing.py
    [ ] Add 4 cron jobs to core/scheduler.py
    [ ] Add !briefing webhook command (manual trigger)
    [ ] Test: !briefing (should generate full morning report)
    [ ] Verify scheduled briefing fires at next cron time

  Phase 5 — Options Flow (Day 3)
    [ ] Create services/options_flow.py
    [ ] Add /api/options endpoints to main.py
    [ ] Add !options, !pcr webhook commands
    [ ] Test: !options AAPL

  Phase 6 — Market Research (Day 3)
    [ ] Create services/market_research.py (aggregates all services)
    [ ] Add /api/research endpoint to main.py
    [ ] Add !research, !dd webhook commands
    [ ] Test: !research UEC (should produce comprehensive report)

  Phase 7 — Testing & Polish (Day 3)
    [ ] End-to-end test: OpenClaw runs !ta, !earnings, !sectors
    [ ] Verify daily briefing schedule fires correctly
    [ ] Check Ollama handles longer prompts without timeout
    [ ] Update !help to include all new commands
    [ ] Restart full stack and verify stability

================================================================================
ESTIMATED TIMELINE
================================================================================

  Day 1: Technical Analysis + Earnings Calendar
  Day 2: Sector Analysis + Daily Briefings
  Day 3: Options Flow + Market Research + Polish

  Total: 3 working sessions

================================================================================
RISKS & MITIGATIONS
================================================================================

  Risk: Moomoo K-line subscription limits (10 per 30 sec)
  Fix:  Cache subscriptions, batch requests, add retry with backoff

  Risk: yfinance rate limiting or data gaps
  Fix:  Add caching layer, fallback to Moomoo quote data

  Risk: Ollama timeouts on large briefing prompts
  Fix:  Set timeout to 120s, truncate input if too long

  Risk: Too many notifications spamming Telegram
  Fix:  Briefings are batched (4x daily), not per-stock

================================================================================
READY TO START?
================================================================================

Say "go" and I will begin with Phase 1 (Technical Analysis + Earnings).
This is the highest-impact work — once done, OpenClaw can immediately
start analyzing RSI/MACD for your UEC, URA, and AAPL positions.

================================================================================
