import axios from "axios";
import { sendTelegramMessage, isTelegramAvailable } from "./telegramService";
import { notificationQueue } from "./notificationQueue";

/**
 * OpenClaw Notification Service
 * Prefers direct Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID) when available.
 * Falls back to OpenClaw proxy gateway for other channels.
 */

export interface OpenClawPayload {
  message: string;
  channel?: string;
  level?: "info" | "warning" | "alert";
}

export const send_openclaw_notification = async (
  payload: OpenClawPayload
): Promise<boolean> => {
  // 1. Prefer direct Telegram when credentials are configured
  if (isTelegramAvailable()) {
    const telegramLevel = payload.level === "alert" ? "warning" : (payload.level ?? "info");
    const ok = await sendTelegramMessage(payload.message, telegramLevel);
    if (ok) return true;
    // fall through to OpenClaw proxy on failure
    console.warn("[OpenClaw] Telegram failed, retrying via OpenClaw proxy...");
  }

  // 2. Enqueue for persistence / OpenClaw heartbeat polling (only when Telegram unavailable or failed)
  try {
    notificationQueue.enqueue(payload.message, payload.level || "info");
  } catch (e) {
    console.error("[OpenClaw] Failed to enqueue notification", e);
  }

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
    console.error(`[OpenClaw] Notification failed: ${error.message || "Unknown error"}`);
    return false;
  }
};

export const send_alert = async (message: string, level: "info" | "warning" | "error" = "info"): Promise<boolean> => {
  return send_openclaw_notification({
    message,
    level: level === "error" ? "alert" : level
  });
};

export const send_trade_execution = async (
  symbol: string,
  side: "BUY" | "SELL",
  qty: number,
  price: number,
  mode: string,
  reason: string,
  pnl?: number
): Promise<boolean> => {
  const pnlStr = pnl !== undefined ? ` | PnL: $${pnl.toFixed(2)}` : "";
  const emoji = side === "BUY" ? "🟢" : "🔴";
  const message = `${emoji} ${side} ${qty} ${symbol} @ $${price} [${mode}] | Reason: ${reason}${pnlStr}`;
  
  return send_openclaw_notification({
    message,
    level: "info",
    channel: "trades"
  });
};

const USD_TO_MYR = 4.72;
const HKD_TO_MYR = 0.59;
const SPEND_PER_TRADE_MYR = 40; // RM40 per trade (20% of RM200 budget)

export const send_trade_signal = async (signal: {
  symbol: string;
  recommendation: string;
  confidence: number;
  currentPrice: number;
  analysis?: string;
  stop_loss?: number;
  take_profit?: number;
  riskLevel?: string;
  technicalIndicators?: { rsi?: number; macd?: string; movingAverage?: string };
  newsReason?: string;
  socialBuzz?: string;
  holdDuration?: string;
}): Promise<boolean> => {
  const isBuy = signal.recommendation === "BUY" || signal.recommendation === "STRONG_BUY";
  const isSell = signal.recommendation === "SELL" || signal.recommendation === "STRONG_SELL";
  if (!isBuy && !isSell) return false;

  const isScalp = signal.analysis?.includes("[SCALP ⚡]");
  const isHK = signal.symbol.toUpperCase().endsWith('.HK') || /^\d{4,5}$/.test(signal.symbol);
  const myrRate = isHK ? HKD_TO_MYR : USD_TO_MYR;
  const priceCurrency = isHK ? "HK$" : "$";
  const entry = signal.currentPrice;

  // Dynamic Stop/Target based on mode
  const stop = signal.stop_loss ?? (isBuy ? (isScalp ? entry * 0.985 : entry * 0.97) : (isScalp ? entry * 1.015 : entry * 1.03));
  const target = signal.take_profit ?? (isBuy ? (isScalp ? entry * 1.025 : entry * 1.06) : (isScalp ? entry * 0.975 : entry * 0.94));

  const riskLevel = signal.riskLevel ?? "MEDIUM";
  const confPct = Math.round(signal.confidence * 100);
  const riskEmoji = riskLevel === "LOW" ? "🟢" : riskLevel === "MEDIUM" ? "🟡" : "🔴";

  const hold = signal.holdDuration ?? (isScalp ? "90-180 min (SCALP)" : "End of day (Intraday)");

  const entryMYR = entry * myrRate;
  const stopMYR = stop * myrRate;
  const targetMYR = target * myrRate;
  const budgetForTrade = SPEND_PER_TRADE_MYR;
  const fractionalQty = (budgetForTrade / entryMYR).toFixed(4);
  const maxLoss = Math.abs(entry - stop) * myrRate * parseFloat(fractionalQty);

  const ind = signal.technicalIndicators;
  const indLine = ind
    ? `RSI: ${ind.rsi?.toFixed(1) ?? "N/A"} | MACD: ${ind.macd ?? "N/A"}`
    : "Indicators unavailable";

  const reasonLines: string[] = [];
  if (signal.analysis) reasonLines.push(`🧠 ${signal.analysis.substring(0, 150)}`);
  if (signal.newsReason) reasonLines.push(`📰 ${signal.newsReason}`);
  if (signal.socialBuzz) reasonLines.push(`💬 Social: ${signal.socialBuzz}`);

  const actionVerb = isBuy ? "BUY" : "SELL SHORT";
  const holdDays = hold.toLowerCase().includes("intraday") ? "today (close before market ends)"
    : hold.toLowerCase().includes("scalp") ? "1–3 hours max"
    : hold;

  const message =
    `━━━━━━━━━━━━━━━━━━━━━━\n` +
    `${isBuy ? "🟢" : "🔴"} **${actionVerb}: $${signal.symbol}**\n` +
    `━━━━━━━━━━━━━━━━━━━━━━\n` +
    `📋 **STATION ACTION:**\n` +
    `• **Buy At:** ${priceCurrency}${entry.toFixed(2)} (≈ RM${entryMYR.toFixed(2)})\n` +
    `• **Target:** ${priceCurrency}${target.toFixed(2)} (+${((target/entry-1)*100).toFixed(1)}%)\n` +
    `• **Stop Loss:** ${priceCurrency}${stop.toFixed(2)} (${((stop/entry-1)*100).toFixed(1)}%)\n` +
    `• **Hold:** ${holdDays}\n` +
    `\n` +
    `🧠 **AI LOGIC:**\n` +
    (reasonLines.length > 0 ? reasonLines.map(l => `• ${l.replace(/[🧠📰💬]/g, '').trim()}`).join("\n") + "\n" : "") +
    `• Confidence: ${confPct}% | Risk: ${riskLevel}\n` +
    `\n` +
    `✅ *Goal: Follow the plan. Cut loss quickly if it hits safety exit.*\n` +
    `━━━━━━━━━━━━━━━━━━━━━━`;

  return send_openclaw_notification({ message, level: "info", channel: "signals" });
};


export const send_daily_summary = async (signals: any[], pnl: number): Promise<boolean> => {
  const pnlEmoji = pnl >= 0 ? "💰" : "📉";
  const pnlStr = pnl >= 0 ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`;
  
  const message = `📊 Daily Summary Report\n` +
    `--------------------------\n` +
    `PnL Today: ${pnlEmoji} ${pnlStr}\n` +
    `Signals Generated: ${signals.length}\n` +
    `--------------------------\n` +
    (signals.length > 0 ? 
      `Latest Signals:\n${signals.slice(0, 5).map(s => `• ${s.symbol}: ${s.recommendation} (${(s.confidence * 100).toFixed(0)}%)`).join('\n')}` : 
      "No signals generated today.");

  return send_openclaw_notification({
    message,
    level: "info",
    channel: "reports"
  });
};
