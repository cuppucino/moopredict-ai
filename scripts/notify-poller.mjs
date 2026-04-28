#!/usr/bin/env node
/**
 * MooPredict Notification Poller (Option B)
 *
 * Polls localhost:3001 for pending notifications, sends each one directly to
 * Telegram using the bot token already configured in OpenClaw (~/.openclaw/openclaw.json).
 * No extra env vars or config changes needed.
 *
 * Usage:
 *   node scripts/notify-poller.mjs
 *
 * Optional .env overrides:
 *   MOOPREDICT_URL       — defaults to http://localhost:3001
 *   TELEGRAM_BOT_TOKEN   — auto-read from ~/.openclaw/openclaw.json if not set
 *   TELEGRAM_CHAT_ID     — auto-detected from OpenClaw config if not set
 *   POLL_INTERVAL_MS     — defaults to 5000 (5 seconds)
 */

import { readFileSync, existsSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { homedir } from "os";

const __dir = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dir, "..");

// ── Load .env manually (no deps) ───────────────────────────────────────────
function loadEnv(filePath) {
  if (!existsSync(filePath)) return;
  const lines = readFileSync(filePath, "utf8").split("\n");
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = val;
  }
}

loadEnv(resolve(ROOT, ".env"));
loadEnv(resolve(ROOT, ".env.local"));

// ── Auto-read credentials from OpenClaw config ─────────────────────────────
function readOpenClawConfig() {
  const configPath = resolve(homedir(), ".openclaw", "openclaw.json");
  if (!existsSync(configPath)) return {};
  try { return JSON.parse(readFileSync(configPath, "utf8")); }
  catch { return {}; }
}

const ocConfig = readOpenClawConfig();

// Ignore placeholder values from .env
function realEnv(key) {
  const val = process.env[key] || "";
  if (!val || val.startsWith("your_") || val === "PLACEHOLDER") return "";
  return val;
}

const BOT_TOKEN =
  realEnv("TELEGRAM_BOT_TOKEN") ||
  ocConfig?.channels?.telegram?.botToken ||
  "";

// Chat ID: from env, or hardcoded from OpenClaw logs: sendMessage ok chat=REMOVED_PRIVATE_VALUE
const CHAT_ID =
  realEnv("TELEGRAM_CHAT_ID") ||
  "REMOVED_PRIVATE_VALUE";

// ── Config ─────────────────────────────────────────────────────────────────
const BASE_URL = process.env.MOOPREDICT_URL || "http://127.0.0.1:3001";
const POLL_MS  = parseInt(process.env.POLL_INTERVAL_MS || "5000", 10);

const LEVEL_EMOJI = { info: "ℹ️", warning: "⚠️", alert: "🚨", error: "🚨" };

if (!BOT_TOKEN) {
  console.error("❌  No Telegram bot token found. Set TELEGRAM_BOT_TOKEN in .env");
  console.error("    (or ensure ~/.openclaw/openclaw.json has channels.telegram.botToken)");
  process.exit(1);
}

// ── Helpers ────────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${path}`);
  return res.json();
}

async function sendTelegram(notif) {
  const emoji = LEVEL_EMOJI[notif.level] ?? "ℹ️";
  const escapedMessage = notif.message
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const text  = `<b>${emoji} MooPredict</b>\n\n${escapedMessage}`;
  const url   = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;

  const res = await fetch(url, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ chat_id: CHAT_ID, text, parse_mode: "HTML" }),
  });

  const data = await res.json();
  if (!data.ok) throw new Error(`Telegram error: ${data.description || JSON.stringify(data)}`);
  return true;
}

async function markSent(id) {
  await apiFetch("/api/notifications/mark-sent", {
    method: "POST",
    body:   JSON.stringify({ id }),
  });
}

// ── Poll loop ──────────────────────────────────────────────────────────────
let consecutiveErrors = 0;
let isPolling = false;

async function poll() {
  // Prevent concurrent executions stacking up if the server is slow
  if (isPolling) return;
  isPolling = true;

  try {
    const pending = await apiFetch("/api/notifications/pending");
    consecutiveErrors = 0;

    if (pending.length === 0) return;

    console.log(`[${new Date().toLocaleTimeString()}] 📬 ${pending.length} pending notification(s)`);

    for (const notif of pending) {
      try {
        await sendTelegram(notif);
        await markSent(notif.id);
        console.log(`  ✅ Sent & marked #${notif.id}: ${notif.message.slice(0, 60)}`);
      } catch (err) {
        console.error(`  ❌  Error on #${notif.id}: ${err.message}`);
      }
    }
  } catch (err) {
    consecutiveErrors++;
    if (consecutiveErrors === 1 || consecutiveErrors % 12 === 0) {
      console.warn(`[${new Date().toLocaleTimeString()}] ⚠️  Server unreachable: ${err.message}`);
    }
  } finally {
    isPolling = false;
  }
}

// ── Start ──────────────────────────────────────────────────────────────────
const tokenHint = BOT_TOKEN ? BOT_TOKEN.slice(0, 8) + "***" : "MISSING";
console.log(`🚀 MooPredict Notification Poller started`);
console.log(`   MooPredict : ${BASE_URL}`);
console.log(`   Telegram   : chat ${CHAT_ID} (token ${tokenHint})`);
console.log(`   Interval   : ${POLL_MS / 1000}s`);
console.log(`   Press Ctrl+C to stop\n`);

poll();
setInterval(poll, POLL_MS);
