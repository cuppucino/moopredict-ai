import express from "express";
import helmet from "helmet";
import { logger } from "./core/Logger.js";
import { initPostgres, query } from "./db/postgres.js";
import { notificationQueue } from "./services/notificationQueue.js";

import { scheduler } from "./services/cron.js";
import { news_scraper } from "./services/newsScraper.js";
import { moomooService } from "./services/moomooService.js";

const app = express();
const port = process.env.PORT ? parseInt(process.env.PORT, 10) : 3001;

app.use(helmet());
app.use(express.json());

// ─── Health ─────────────────────────────────────────────────────────────────
app.get("/health", (_req, res) => res.json({ ok: true, uptime: process.uptime() }));

// ─── Notifications (for notify-poller.mjs) ───────────────────────────────────
app.get("/api/notifications/pending", (_req, res) => {
  res.json(notificationQueue.getPending());
});

app.post("/api/notifications/mark-sent", (req, res) => {
  notificationQueue.markAsSent(req.body?.id);
  res.json({ success: true });
});

app.post("/api/notifications/dequeue", (req, res) => {
  notificationQueue.markAsSent(req.body?.id);
  res.json({ success: true });
});

// ─── Watchlist API ───────────────────────────────────────────────────────────
app.get("/api/watchlist", async (_req, res) => {
  const result = await query("SELECT symbol FROM user_watchlist ORDER BY added_at DESC");
  res.json(result.rows.map((r: any) => r.symbol));
});

app.post("/api/watchlist", async (req, res) => {
  const symbol = (req.body?.symbol || "").toUpperCase().trim();
  if (!symbol) return res.status(400).json({ error: "symbol required" });
  await query("INSERT INTO user_watchlist (symbol) VALUES ($1) ON CONFLICT (symbol) DO NOTHING", [symbol]);
  res.json({ success: true, symbol });
});

app.delete("/api/watchlist/:symbol", async (req, res) => {
  const symbol = req.params.symbol.toUpperCase();
  await query("DELETE FROM user_watchlist WHERE symbol = $1", [symbol]);
  res.json({ success: true, symbol });
});

// ─── Webhook (Commands from OpenClaw) ────────────────────────────────────────
app.post("/webhook", async (req, res) => {
  try {
    const text: string = (req.body?.text || req.body?.content || "").trim();
    const parts = text.split(/\s+/);
    const command = parts[0].toLowerCase();
    logger.info(`[Webhook] Received: ${text}`);

    // !checkstatus — live positions + balance from Moomoo
    if (command === "!checkstatus" || command === "!positions" || command === "!pos") {
      const [positions, balance] = await Promise.all([
        moomooService.getPositions(),
        moomooService.getBalance(),
      ]);

      const lines = positions.length > 0
        ? positions.map(p => `• ${p.symbol}: ${p.qty} shares @ $${p.avgPrice} | P&L: ${p.pnl}`).join("\n")
        : "No open positions.";

      return res.json({
        content:
          `📊 *Moomoo Status*\n` +
          `──────────────────\n` +
          `💵 Cash: $${balance?.availableCash?.toLocaleString() ?? 'N/A'}\n` +
          `📈 Total Assets: $${balance?.totalAssets?.toLocaleString() ?? 'N/A'}\n\n` +
          `*Positions:*\n${lines}`
      });
    }

    // !watchlist — show saved watchlist
    if (command === "!watchlist") {
      const result = await query("SELECT symbol FROM user_watchlist ORDER BY added_at DESC");
      const symbols: string[] = result.rows.map((r: any) => r.symbol);
      const list = symbols.length > 0 ? symbols.map(s => `• ${s}`).join("\n") : "Watchlist is empty.";
      return res.json({ content: `📋 *Watchlist*\n──────────────\n${list}` });
    }

    // !add SYMBOL — add to watchlist
    if (command === "!add" && parts[1]) {
      const symbol = parts[1].toUpperCase();
      await query("INSERT INTO user_watchlist (symbol) VALUES ($1) ON CONFLICT (symbol) DO NOTHING", [symbol]);
      return res.json({ content: `✅ Added *${symbol}* to watchlist.` });
    }

    // !remove SYMBOL — remove from watchlist
    if ((command === "!remove" || command === "!rm") && parts[1]) {
      const symbol = parts[1].toUpperCase();
      await query("DELETE FROM user_watchlist WHERE symbol = $1", [symbol]);
      return res.json({ content: `🗑️ Removed *${symbol}* from watchlist.` });
    }

    // !news — trigger immediate scrape
    if (command === "!news") {
      news_scraper.run();
      return res.json({ content: "🚀 News scrape triggered!" });
    }

    // !status — server health
    if (command === "!status") {
      const wl = await query("SELECT COUNT(*) as c FROM user_watchlist");
      return res.json({
        content:
          `✅ *Server Online*\n` +
          `⏱ Uptime: ${Math.floor(process.uptime() / 60)}m\n` +
          `📋 Watchlist: ${wl.rows[0].c} symbols\n` +
          `🔗 Moomoo: ${moomooService.isConnected ? 'connected' : 'not connected'}`
      });
    }

    // !help
    if (command === "!help") {
      return res.json({
        content:
          `*Commands:*\n` +
          `• !checkstatus — positions & balance\n` +
          `• !watchlist — show watchlist\n` +
          `• !add SYMBOL — add to watchlist\n` +
          `• !remove SYMBOL — remove from watchlist\n` +
          `• !news — trigger news scrape\n` +
          `• !status — server health`
      });
    }

    res.json({ content: "Unknown command. Try !help" });
  } catch (e: any) {
    logger.error(`[Webhook] Error: ${e.message}`);
    res.status(500).json({ error: "Internal server error" });
  }
});

// ─── Startup ────────────────────────────────────────────────────────────────
const start = async () => {
  logger.info("🚀 Starting MooPredict...");

  try {
    await initPostgres();
    await moomooService.connect();
    scheduler.init();
    // startPolling() disabled — OpenClaw handles Telegram and forwards to /webhook
    app.listen(port, "0.0.0.0", () => {
      logger.info(`[Server] ONLINE: http://0.0.0.0:${port}`);
    });
  } catch (error: any) {
    logger.error(`[Server] Startup failure: ${error.message}`);
    process.exit(1);
  }
};

start();
