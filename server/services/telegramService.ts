import axios from "axios";
import fs from 'fs';
import { logger } from "../core/Logger.js";
import { config } from "../core/Config.js";

/**
 * Enterprise Telegram Service.
 * 
 * Provides direct Telegram bot communication.
 */

const AVAILABLE = !!(config.TELEGRAM_BOT_TOKEN && config.TELEGRAM_CHAT_ID);

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

  try {
    const escapedMessage = message
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    const text = `<b>${LEVEL_PREFIX[level]} MooPredict</b>\n\n${escapedMessage}`;
    const url  = `https://api.telegram.org/bot${config.TELEGRAM_BOT_TOKEN}/sendMessage`;

    const response = await axios.post(url, {
      chat_id:    config.TELEGRAM_CHAT_ID,
      text,
      parse_mode: 'HTML',
    }, { timeout: 5000 });

    if (response.data?.ok === true) {
      logger.info("[Telegram] Message sent successfully");
      return true;
    }
    return false;
  } catch (error: any) {
    logger.error(`[Telegram] Failed to send: ${error.message}`);
    return false;
  }
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
};

/**
 * True when TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set.
 * Use this to decide whether to prefer direct Telegram or fall back to OpenClaw.
 */
export const isTelegramAvailable = (): boolean => AVAILABLE;

let last_update_id = 0;
let is_polling = false;

/**
 * Start polling for Telegram commands.
 * Listens for messages starting with '!' and forwards them to the local /webhook endpoint.
 */
export const startPolling = async () => {
  if (!AVAILABLE || is_polling) return;
  is_polling = true;
  logger.info("[Telegram] Starting command polling...");

  const poll = async () => {
    try {
      const url = `https://api.telegram.org/bot${config.TELEGRAM_BOT_TOKEN}/getUpdates?offset=${last_update_id + 1}&timeout=20`;
      const response = await axios.get(url, { timeout: 25000 });

      if (response.data?.ok && response.data.result.length > 0) {
        for (const update of response.data.result) {
          last_update_id = update.update_id;
          const text = update.message?.text;
          const chatId = update.message?.chat?.id;
          const username = update.message?.from?.username || "unknown";

          if (chatId) {
            logger.info(`[Telegram] Incoming from ${username} (${chatId}): ${text}`);
          }

          if (text?.startsWith('!') && chatId?.toString() === config.TELEGRAM_CHAT_ID?.toString()) {
            logger.info(`[Telegram] Command received: ${text}`);
            
            // Forward to local webhook
            try {
              // Note: Using 127.0.0.1:3001 as defined in server.ts
              const res = await axios.post('http://127.0.0.1:3001/webhook', 
                { content: text }, 
                { timeout: 30000 }
              );
              
              if (res.data?.content) {
                await sendTelegramMessage(res.data.content);
              }
            } catch (e: any) {
              logger.error(`[Telegram] Webhook forwarding failed: ${e.message}`);
              await sendTelegramMessage(`❌ Command execution failed: ${e.message}`);
            }
          }
        }
      }
    } catch (error: any) {
      if (error.code !== 'ECONNABORTED' && error.code !== 'ETIMEDOUT') {
        logger.error(`[Telegram] Polling error: ${error.message}`);
      }
    } finally {
      // Continue polling
      setTimeout(poll, 2000);
    }
  };

  poll();
};
