import { z } from 'zod';
import dotenv from 'dotenv';
import { logger } from './Logger.js';

dotenv.config();

const ConfigSchema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),

  // Moomoo / OpenD
  // When running inside Docker, override MOOMOO_HOST to host.docker.internal
  MOOMOO_HOST: z.string().default('127.0.0.1'),
  MOOMOO_PORT: z.coerce.number().default(11111),
  MOOMOO_WS_PORT: z.coerce.number().default(33333),
  MOOMOO_WEBSOCKET_KEY: z.string().optional(),
  MOOMOO_ACCOUNT: z.string().optional(),
  MOOMOO_PASSWORD_MD5: z.string().optional(),

  // Telegram / OpenClaw
  TELEGRAM_BOT_TOKEN: z.string().optional(),
  TELEGRAM_CHAT_ID: z.string().optional(),
  OPENCLAW_WEBHOOK_URL: z.string().url().default('http://127.0.0.1:18789/webhook'),
});

export type Config = z.infer<typeof ConfigSchema>;

let parsedConfig: Config;

try {
  parsedConfig = ConfigSchema.parse(process.env);
  logger.info('[Config] Configuration loaded.', {
    moomoo: `${parsedConfig.MOOMOO_HOST}:${parsedConfig.MOOMOO_PORT}`,
    telegram: parsedConfig.TELEGRAM_BOT_TOKEN ? 'configured' : 'not set',
  });
} catch (error) {
  if (error instanceof z.ZodError) {
    logger.error('[Config] Configuration validation failed:', {
      errors: error.issues.map(i => `${i.path.join('.')}: ${i.message}`)
    });
    if (process.env.NODE_ENV === 'production') process.exit(1);
  }
  parsedConfig = ConfigSchema.parse({});
}

export const config = parsedConfig;
