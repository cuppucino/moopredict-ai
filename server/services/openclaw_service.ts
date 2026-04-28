import axios from "axios";
import { sendTelegramMessage, isTelegramAvailable } from "./telegramService.js";
import { notificationQueue } from "./notificationQueue.js";

/**
 * OpenClaw Notification Service (Simplified)
 */

export interface OpenClawPayload {
  message: string;
  channel?: string;
  level?: "info" | "warning" | "alert";
}

export const send_openclaw_notification = async (
  payload: OpenClawPayload
): Promise<boolean> => {
  // 1. Prefer direct Telegram when available
  if (isTelegramAvailable()) {
    const telegramLevel = payload.level === "alert" ? "warning" : (payload.level ?? "info");
    const ok = await sendTelegramMessage(payload.message, telegramLevel);
    if (ok) return true;
  }

  // 2. Fallback: Enqueue for notify-poller.mjs and try direct webhook
  notificationQueue.enqueue(payload.message, payload.level || "info");

  try {
    const openclaw_url = process.env.OPENCLAW_WEBHOOK_URL || "http://127.0.0.1:18789/webhook";
    const request_body = {
      content: payload.message,
      metadata: {
        channel: payload.channel || "default",
        level: payload.level || "info",
        source: "moopredict-ai",
      },
    };

    const response = await axios.post(openclaw_url, request_body, {
      headers: { "Content-Type": "application/json" },
      timeout: 5000,
    });

    return response.status >= 200 && response.status < 300;
  } catch (error: any) {
    // Silently fail if webhook is down, poller will handle it if configured
    return false;
  }
};

export const send_alert = async (message: string, level: "info" | "warning" | "error" = "info"): Promise<boolean> => {
  return send_openclaw_notification({
    message,
    level: level === "error" ? "alert" : level
  });
};
