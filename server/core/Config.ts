import { z } from 'zod';
import dotenv from 'dotenv';
import { logger } from './Logger';

dotenv.config();

/**
 * Enterprise Config Management with Runtime Validation (Zod).
 * 
 * Centralizes all thresholds, timeouts, and credentials.
 * Ensures the system fails fast if critical variables are missing or malformed.
 */

const ConfigSchema = z.object({
  // --- Trading Environment ---
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  FUTU_TRADE_MODE: z.enum(['PAPER', 'LIVE']).default('PAPER'),
  FUTU_HOST: z.string().default('127.0.0.1'),
  FUTU_PORT: z.coerce.number().default(11111),

  // --- External Services ---
  OLLAMA_URL: z.string().url().default('http://localhost:11434'),
  OLLAMA_MODEL: z.string().default('llama3.2'),
  
  // --- Telegram / OpenClaw ---
  TELEGRAM_BOT_TOKEN: z.string().optional(),
  TELEGRAM_CHAT_ID: z.string().optional(),
  OPENCLAW_WEBHOOK_URL: z.string().url().default('http://localhost:18789/webhook'),

  // --- Market Config ---
  TRADE_MARKET: z.enum(['US']).default('US'),
  TRADE_CURRENCY: z.string().default('USD'),

  // --- Trading Thresholds ---
  // Symbols with poor backtest performance that should never be auto-traded.
  TRADE_SYMBOL_BLOCKLIST: z.string().default('').transform(s =>
    s.split(',').map(x => x.trim().toUpperCase()).filter(Boolean)
  ),
  SIGNAL_CONFIDENCE_MIN: z.coerce.number().min(0).max(1).default(0.50),
  SIGNAL_CONFIDENCE_HIGH: z.coerce.number().min(0).max(1).default(0.70),
  RISK_BLOCK_THRESHOLD: z.coerce.number().min(0).max(100).default(90),
  MAX_DAILY_LOSS_USD: z.coerce.number().default(500),
  TRADE_LOT_VALUE_USD: z.coerce.number().default(2000),
  MAX_CONCURRENT_POSITIONS: z.coerce.number().default(10),

  // --- Position Management ---
  STOP_LOSS_FLOOR_PCT: z.coerce.number().default(-5),
  TAKE_PROFIT_CEILING_PCT: z.coerce.number().default(12),
  MAX_HOLD_DAYS: z.coerce.number().default(5),
  TRAILING_STOP_ACTIVATION_PCT: z.coerce.number().default(3),
  TRAILING_STOP_TRAIL_PCT: z.coerce.number().default(5),
  
  // Automated Monitor Thresholds (Global %)
  TAKE_PROFIT_PCT: z.coerce.number().default(3.0),
  STOP_LOSS_PCT: z.coerce.number().default(2.0),

  // Earnings Awareness
  EARNINGS_BLACKOUT_DAYS: z.coerce.number().default(2),

  // --- Timing (ms) ---
  SCOUT_INTERVAL_MS: z.coerce.number().default(60 * 60 * 1000),      // 60 min
  EXECUTOR_CHECK_INTERVAL_MS: z.coerce.number().default(15 * 60 * 1000), // 15 min
});

export type Config = z.infer<typeof ConfigSchema>;

let parsedConfig: Config;

try {
  parsedConfig = ConfigSchema.parse(process.env);
  logger.info('[Config] Runtime configuration validated successfully.', {
    mode: parsedConfig.FUTU_TRADE_MODE,
    ollama: parsedConfig.OLLAMA_MODEL,
    confidence_min: parsedConfig.SIGNAL_CONFIDENCE_MIN
  });
} catch (error) {
  if (error instanceof z.ZodError) {
    logger.error('[Config] CRITICAL: Configuration validation failed:', {
      errors: error.issues.map(i => `${i.path.join('.')}: ${i.message}`)
    });
    // In production, we should exit if config is invalid
    if (process.env.NODE_ENV === 'production') {
      process.exit(1);
    }
  }
  // Fallback to defaults for non-production
  parsedConfig = ConfigSchema.parse({});
}

export const config = parsedConfig;
