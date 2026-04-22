import axios from "axios";
import fs from 'fs';
import { logger } from "../core/Logger";
import { config } from "../core/Config";
import { CircuitBreaker } from "../core/CircuitBreaker";

/**
 * Enterprise Telegram Service.
 * 
 * Provides direct Telegram bot communication with circuit breaker protection.
 */

const AVAILABLE = !!(config.TELEGRAM_BOT_TOKEN && config.TELEGRAM_CHAT_ID);
const breaker = new CircuitBreaker({ name: 'Telegram', failureThreshold: 3, resetTimeoutMs: 60000 });

export type TelegramLevel = 'info' | 'warning' | 'important' | 'critical';

const LEVEL_PREFIX: Record<TelegramLevel, string> = {
  info:      'ℹ️',
  warning:   '⚠️',
  important: '⚠️',
  critical:  '🚨',
};

/**
 * Send a message directly to Telegram.
 * Returns true on success, false on failure.
 */
export const sendTelegramMessage = async (
  message: string,
  level: TelegramLevel = 'info'
): Promise<boolean> => {
  if (!AVAILABLE) {
    logger.warn("[Telegram] Service skipped (Missing BOT_TOKEN or CHAT_ID)");
    return false;
  }

  return breaker.execute(async () => {
    try {
      const text = `${LEVEL_PREFIX[level]} *MooPredict*\n\n${message}`;
      const url  = `https://api.telegram.org/bot${config.TELEGRAM_BOT_TOKEN}/sendMessage`;

      const response = await axios.post(url, {
        chat_id:    config.TELEGRAM_CHAT_ID,
        text,
        parse_mode: 'Markdown',
      }, { timeout: 5000 });

      return response.data?.ok === true;
    } catch (error: any) {
      logger.error(`[Telegram] Failed to send: ${error.message}`);
      return false;
    }
  });
};

/**
 * Send a local photo file to Telegram.
 * Useful for assistant portraits, charts, and analysis screenshots.
 */
export const sendTelegramPhoto = async (
  photoPath: string,
  caption?: string
): Promise<boolean> => {
  if (!AVAILABLE) return false;

  return breaker.execute(async () => {
    try {
      if (!fs.existsSync(photoPath)) {
        throw new Error(`File not found: ${photoPath}`);
      }

      const fileData = fs.readFileSync(photoPath);
      const url = `https://api.telegram.org/bot${config.TELEGRAM_BOT_TOKEN}/sendPhoto`;

      // Use modern Node FormData if available (Node 18+), otherwise manual construction
      // Actually, axios handles it well if we provide a Buffer in a FormData object
      const formData = new FormData();
      formData.append('chat_id', config.TELEGRAM_CHAT_ID as string);
      formData.append('photo', new Blob([fileData]), 'image.png');
      if (caption) {
        formData.append('caption', caption);
        formData.append('parse_mode', 'Markdown');
      }

      const response = await axios.post(url, formData, {
        timeout: 15000
      });

      return response.data?.ok === true;
    } catch (error: any) {
      logger.error(`[Telegram] Photo send failed: ${error.message}`);
      return false;
    }
  });
};

/**
 * True when TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set.
 * Use this to decide whether to prefer direct Telegram or fall back to OpenClaw.
 */
export const isTelegramAvailable = (): boolean => AVAILABLE;
