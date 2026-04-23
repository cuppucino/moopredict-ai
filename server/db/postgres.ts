import { Pool, PoolClient } from 'pg';
import dotenv from 'dotenv';

dotenv.config();

// PostgreSQL connection pool
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false,
  max: 20, // Maximum number of clients in the pool
  idleTimeoutMillis: 30000, // Close idle clients after 30 seconds
  connectionTimeoutMillis: 2000, // Return error after 2 seconds if connection not established
});

// Log connection status
pool.on('connect', () => {
  console.log('[PostgreSQL] New client connected');
});

pool.on('error', (err: Error) => {
  console.error('[PostgreSQL] Unexpected error on idle client', err);
});

// Initialize database tables
export async function initPostgres(): Promise<void> {
  const client = await pool.connect();
  
  try {
    console.log('[PostgreSQL] Initializing tables...');
    
    // Watchlist table
    await client.query(`
      CREATE TABLE IF NOT EXISTS watchlist (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) UNIQUE NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    
    // Bots table
    await client.query(`
      CREATE TABLE IF NOT EXISTS bots (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        mode VARCHAR(20) NOT NULL,
        status VARCHAR(20) DEFAULT 'STOPPED',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, mode)
      )
    `);
    
    // Positions table
    await client.query(`
      CREATE TABLE IF NOT EXISTS positions (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        mode VARCHAR(20) NOT NULL,
        qty INTEGER NOT NULL,
        entry_price DECIMAL(10, 2) NOT NULL,
        side VARCHAR(10) DEFAULT 'LONG',
        stop_loss_pct DECIMAL(5, 2) DEFAULT -5.00,
        take_profit_pct DECIMAL(5, 2) DEFAULT 10.00,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    
    // Trade history table
    await client.query(`
      CREATE TABLE IF NOT EXISTS trade_history (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        mode VARCHAR(20) NOT NULL,
        side VARCHAR(10) NOT NULL,
        qty INTEGER NOT NULL,
        price DECIMAL(10, 2) NOT NULL,
        reason TEXT,
        pnl DECIMAL(10, 2),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    
    // Daily PnL table
    await client.query(`
      CREATE TABLE IF NOT EXISTS daily_pnl (
        date DATE PRIMARY KEY,
        total_pnl DECIMAL(10, 2) DEFAULT 0.00,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    
    // Bot profiles table
    await client.query(`
      CREATE TABLE IF NOT EXISTS bot_profiles (
        symbol VARCHAR(20) PRIMARY KEY,
        name VARCHAR(100),
        personality VARCHAR(20),
        strategy TEXT,
        level INTEGER DEFAULT 1,
        xp INTEGER DEFAULT 0,
        energy INTEGER DEFAULT 100,
        total_trades INTEGER DEFAULT 0,
        profitable_trades INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // Signals table
    await client.query(`
      CREATE TABLE IF NOT EXISTS signals (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        date DATE NOT NULL,
        recommendation VARCHAR(20) NOT NULL,
        confidence DECIMAL(5, 2) NOT NULL,
        data_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, date)
      )
    `);

    // News articles table
    await client.query(`
      CREATE TABLE IF NOT EXISTS news_articles (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        headline TEXT NOT NULL,
        source VARCHAR(50) NOT NULL,
        url TEXT,
        summary TEXT,
        published_at TIMESTAMP,
        sentiment_score DECIMAL(5, 2) DEFAULT 0,
        sentiment_label VARCHAR(20) DEFAULT 'neutral',
        keywords TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // Unique index on news_articles.url — required for ON CONFLICT (url) in analyzeSentiment()
    await client.query(`
      CREATE UNIQUE INDEX IF NOT EXISTS idx_news_articles_url
      ON news_articles(url)
      WHERE url IS NOT NULL AND url != ''
    `);

    // Social mentions table
    await client.query(`
      CREATE TABLE IF NOT EXISTS social_mentions (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        platform VARCHAR(20) NOT NULL,
        content TEXT NOT NULL,
        author VARCHAR(100),
        url TEXT,
        upvotes INTEGER DEFAULT 0,
        comments INTEGER DEFAULT 0,
        sentiment_score DECIMAL(5, 2) DEFAULT 0,
        sentiment_label VARCHAR(20) DEFAULT 'neutral',
        virality_score DECIMAL(5, 2) DEFAULT 0,
        mentioned_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // Ensure url has a unique constraint so ON CONFLICT (url) works
    await client.query(`
      CREATE UNIQUE INDEX IF NOT EXISTS social_mentions_url_unique
      ON social_mentions (url)
    `);

    // Risk snapshots table
    await client.query(`
      CREATE TABLE IF NOT EXISTS risk_snapshots (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        risk_score DECIMAL(5, 2) DEFAULT 50,
        risk_factors TEXT,
        market_regime VARCHAR(50),
        volatility_percentile DECIMAL(5, 2),
        news_sentiment_avg DECIMAL(5, 2),
        social_sentiment_avg DECIMAL(5, 2),
        technical_risk TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // Model predictions table
    await client.query(`
      CREATE TABLE IF NOT EXISTS model_predictions (
        id SERIAL PRIMARY KEY,
        model_name VARCHAR(50) NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        predicted_direction VARCHAR(10),
        confidence DECIMAL(5, 2),
        features_used TEXT,
        actual_direction VARCHAR(10),
        correct BOOLEAN,
        predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP
      )
    `);

    // System config table
    await client.query(`
      CREATE TABLE IF NOT EXISTS system_config (
        key VARCHAR(100) PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // Manual positions table
    await client.query(`
      CREATE TABLE IF NOT EXISTS manual_positions (
        id                SERIAL PRIMARY KEY,
        symbol            TEXT NOT NULL,
        entry_price       DECIMAL(12, 4) NOT NULL,
        quantity          INTEGER NOT NULL,
        entry_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status            TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN | CLOSED | ADVISED_EXIT
        exit_price        DECIMAL(12, 4),
        exit_time         TIMESTAMP,
        exit_reason       TEXT,
        pnl_absolute      DECIMAL(12, 4),
        pnl_pct           DECIMAL(8, 4),
        notes             TEXT,
        entry_context     JSONB,  -- market snapshot at entry (RSI, MACD, confidence, news_score)
        agent_advice      JSONB DEFAULT '[]',
        auto_exit         BOOLEAN DEFAULT FALSE
      )
    `);

    // Migration block for existing tables
    await client.query(`
      DO $$ 
      BEGIN 
        -- Watchlist
        ALTER TABLE watchlist ALTER COLUMN symbol TYPE VARCHAR(20);
        -- Bots
        ALTER TABLE bots ALTER COLUMN symbol TYPE VARCHAR(20);
        ALTER TABLE bots ALTER COLUMN mode TYPE VARCHAR(20);
        -- Positions
        ALTER TABLE positions ALTER COLUMN symbol TYPE VARCHAR(20);
        ALTER TABLE positions ALTER COLUMN mode TYPE VARCHAR(20);
        ALTER TABLE positions ALTER COLUMN side TYPE VARCHAR(10);
        -- Trade history
        ALTER TABLE trade_history ALTER COLUMN symbol TYPE VARCHAR(20);
        ALTER TABLE trade_history ALTER COLUMN mode TYPE VARCHAR(20);
        ALTER TABLE trade_history ALTER COLUMN side TYPE VARCHAR(10);
        -- Bot profiles
        ALTER TABLE bot_profiles ALTER COLUMN symbol TYPE VARCHAR(20);
        -- Signals
        ALTER TABLE signals ALTER COLUMN symbol TYPE VARCHAR(20);
        -- News articles
        ALTER TABLE news_articles ALTER COLUMN symbol TYPE VARCHAR(20);
        -- Social mentions
        ALTER TABLE social_mentions ALTER COLUMN symbol TYPE VARCHAR(20);
        -- Risk snapshots
        ALTER TABLE risk_snapshots ALTER COLUMN symbol TYPE VARCHAR(20);
        -- Model predictions
        ALTER TABLE model_predictions ALTER COLUMN symbol TYPE VARCHAR(20);

        -- Ensure auto_exit column exists for manual_positions
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='manual_positions' AND column_name='auto_exit') THEN
          ALTER TABLE manual_positions ADD COLUMN auto_exit BOOLEAN DEFAULT FALSE;
        END IF;
      END $$;
    `);

    // Trade training data table
    await client.query(`
      CREATE TABLE IF NOT EXISTS trade_training_data (
        id                      SERIAL PRIMARY KEY,
        position_id             INTEGER REFERENCES manual_positions(id),
        symbol                  TEXT NOT NULL,
        entry_price             DECIMAL(12, 4),
        exit_price              DECIMAL(12, 4),
        pnl_pct                 DECIMAL(8, 4),
        outcome                 TEXT,  -- WIN | LOSS | BREAKEVEN
        entry_rsi               DECIMAL(6, 2),
        entry_macd              DECIMAL(12, 6),
        entry_ma_cross          TEXT,  -- BULLISH | BEARISH | NEUTRAL
        entry_news_score        DECIMAL(6, 4),
        entry_social_buzz       TEXT,  -- LOW | MEDIUM | HIGH | VIRAL
        entry_signal_confidence DECIMAL(6, 4),
        exit_rsi                DECIMAL(6, 2),
        exit_news_score         DECIMAL(6, 4),
        agent_advised_exit      BOOLEAN DEFAULT FALSE,
        agent_exit_confidence   DECIMAL(6, 4),
        agent_advice_was_correct BOOLEAN,
        hold_duration_minutes   INTEGER,
        created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // Signal Outcomes table (Phase 3)
    await client.query(`
      CREATE TABLE IF NOT EXISTS signal_outcomes (
        id              SERIAL PRIMARY KEY,
        correlation_id  UUID NOT NULL,
        symbol          VARCHAR(20) NOT NULL,
        action          VARCHAR(10) NOT NULL,         -- BUY / SELL
        entry_price     DECIMAL(12, 4) NOT NULL,
        entry_time      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        confidence      DECIMAL(4, 3) NOT NULL,
        bull_score      INT NOT NULL,
        bear_score      INT NOT NULL,
        signal_count    INT NOT NULL,
        -- Filled in after outcome check
        exit_price      DECIMAL(12, 4),
        exit_time       TIMESTAMPTZ,
        pnl_pct         DECIMAL(8, 4),               -- (exit - entry) / entry * 100
        outcome         VARCHAR(10),                  -- WIN / LOSS / SCRATCH
        reasoning       TEXT,                         -- The bot's logic
        conviction      VARCHAR(20),                  -- NORMAL / HIGH
        checked         BOOLEAN DEFAULT FALSE
      )
    `);

    // Ensure reasoning/conviction columns exist for existing tables
    await client.query(`
      DO $$ 
      BEGIN 
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='reasoning') THEN
          ALTER TABLE signal_outcomes ADD COLUMN reasoning TEXT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='conviction') THEN
          ALTER TABLE signal_outcomes ADD COLUMN conviction VARCHAR(20);
        END IF;
      END $$;
    `);

    // Macro Event tracking table
    await client.query(`
      CREATE TABLE IF NOT EXISTS macro_events (
        id           SERIAL PRIMARY KEY,
        headline     TEXT NOT NULL,
        source       TEXT NOT NULL,
        category     VARCHAR(20) NOT NULL,
        severity     VARCHAR(10) NOT NULL,
        analysis_json TEXT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);
    await client.query(`CREATE INDEX IF NOT EXISTS idx_macro_severity ON macro_events(severity)`);
    await client.query(`CREATE INDEX IF NOT EXISTS idx_macro_created  ON macro_events(created_at)`);
    await client.query(`CREATE INDEX IF NOT EXISTS idx_macro_category ON macro_events(category)`);

    // Agent Learnings table
    await client.query(`
      CREATE TABLE IF NOT EXISTS agent_learnings (
          id SERIAL PRIMARY KEY,
          date DATE UNIQUE NOT NULL,
          market VARCHAR(10) DEFAULT 'HK',
          total_gain DECIMAL(15,2) DEFAULT 0.00,
          total_loss DECIMAL(15,2) DEFAULT 0.00,
          lesson TEXT,
          top_performer TEXT,
          worst_performer TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // --- MooPredict V4 Tables ---

    // news_intel table (Team A)
    await client.query(`
      CREATE TABLE IF NOT EXISTS news_intel (
        id SERIAL PRIMARY KEY,
        headline TEXT NOT NULL,
        summary TEXT,
        source VARCHAR(50),
        url TEXT UNIQUE,
        mentioned_tickers TEXT[],
        sector VARCHAR(50),
        sentiment_score DECIMAL(5, 2),
        scraped_at TIMESTAMPTZ DEFAULT NOW()
      )
    `);

    // social_intel table (Team A)
    await client.query(`
      CREATE TABLE IF NOT EXISTS social_intel (
        id SERIAL PRIMARY KEY,
        platform VARCHAR(20),
        content TEXT,
        mentioned_tickers TEXT[],
        sentiment_score DECIMAL(5, 2),
        virality_score DECIMAL(5, 2),
        post_count INTEGER DEFAULT 1,
        scraped_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(platform, content)
      )
    `);

    // active_focus table (Team B)
    await client.query(`
      CREATE TABLE IF NOT EXISTS active_focus (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        reason TEXT,
        catalyst_type VARCHAR(30),
        direction VARCHAR(10),
        impact_score DECIMAL(5, 2),
        added_at TIMESTAMPTZ DEFAULT NOW(),
        removed_at TIMESTAMPTZ,
        status VARCHAR(20) DEFAULT 'ACTIVE'
      )
    `);

    // trade_proposals table (Team C)
    await client.query(`
      CREATE TABLE IF NOT EXISTS trade_proposals (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        action VARCHAR(10),
        entry_price DECIMAL(12, 4),
        best_target DECIMAL(12, 4),
        safe_target DECIMAL(12, 4),
        stop_loss DECIMAL(12, 4),
        max_hold_days INTEGER DEFAULT 5,
        hold_for_event BOOLEAN DEFAULT FALSE,
        event_description TEXT,
        confidence DECIMAL(4, 3),
        catalyst_id INTEGER REFERENCES active_focus(id),
        status VARCHAR(20) DEFAULT 'PENDING',
        created_at TIMESTAMPTZ DEFAULT NOW()
      )
    `);

    // virtual_portfolio table (Team D)
    await client.query(`
      CREATE TABLE IF NOT EXISTS virtual_portfolio (
        id SERIAL PRIMARY KEY,
        currency VARCHAR(5) UNIQUE NOT NULL,
        starting_balance DECIMAL(14, 2) NOT NULL,
        current_balance DECIMAL(14, 2) NOT NULL,
        total_pnl DECIMAL(14, 2) DEFAULT 0,
        total_pnl_pct DECIMAL(8, 4) DEFAULT 0,
        updated_at TIMESTAMPTZ DEFAULT NOW()
      )
    `);

    // Seed virtual_portfolio if empty
    await client.query(`
      INSERT INTO virtual_portfolio (currency, starting_balance, current_balance)
      VALUES 
        ('HKD', 50000.00, 50000.00),
        ('USD', 5000.00, 5000.00)
      ON CONFLICT (currency) DO NOTHING
    `);

    // audit_log table (Team D)
    await client.query(`
      CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY,
        proposal_id INTEGER REFERENCES trade_proposals(id),
        symbol VARCHAR(20) NOT NULL,
        was_accurate BOOLEAN,
        predicted_direction VARCHAR(10),
        actual_direction VARCHAR(10),
        predicted_pnl_pct DECIMAL(8, 4),
        actual_pnl_pct DECIMAL(8, 4),
        failure_reason VARCHAR(30),
        lesson TEXT,
        closed_at TIMESTAMPTZ DEFAULT NOW()
      )
    `);

    // Default configs
    await client.query(`
      INSERT INTO system_config (key, value) VALUES
      ('risk_threshold_pause', '75'),
      ('max_portfolio_heat', '0.5'),
      ('ollama_enabled', 'false'),
      ('ollama_url', 'http://localhost:11434'),
      ('ollama_default_model', 'llama3.2')
      ON CONFLICT (key) DO NOTHING
    `);
    
    // etf_signals table
    await client.query(`
      CREATE TABLE IF NOT EXISTS etf_signals (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        signal_type VARCHAR(10) NOT NULL, -- BUY / SELL / WATCH
        entry_price DECIMAL(12, 4),
        target_price DECIMAL(12, 4),
        stop_loss DECIMAL(12, 4),
        confidence DECIMAL(4, 3),
        reason TEXT,
        timeframe VARCHAR(20),
        created_at TIMESTAMPTZ DEFAULT NOW(),
        alerted_at TIMESTAMPTZ,
        UNIQUE(symbol, signal_type, created_at::DATE)
      )
    `);

    console.log('[PostgreSQL] Tables initialized successfully');
  } catch (error) {
    console.error('[PostgreSQL] Error initializing tables:', error);
    throw error;
  } finally {
    client.release();
  }
}

// Query helper
export async function query(text: string, params?: any[]): Promise<any> {
  const start = Date.now();
  try {
    const result = await pool.query(text, params);
    const duration = Date.now() - start;
    console.log('[PostgreSQL] Query executed:', { text: text.substring(0, 50), duration, rows: result.rowCount });
    return result;
  } catch (error) {
    console.error('[PostgreSQL] Query error:', error);
    throw error;
  }
}

// Get client from pool (for transactions)
export async function getClient(): Promise<PoolClient> {
  return pool.connect();
}

// Close pool (for graceful shutdown)
export async function closePool(): Promise<void> {
  await pool.end();
  console.log('[PostgreSQL] Pool closed');
}

export default pool;
