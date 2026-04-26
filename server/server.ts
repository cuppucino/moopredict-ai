import { init_db } from "./db/database";
import express, { Request, Response, NextFunction } from "express";
import helmet from "helmet";
import axios from "axios";
import { z } from "zod";
import { v4 as uuidv4 } from "uuid";
import { futu_service } from "./services/futuService";
import { ollamaService } from "./services/ollamaService";
import { ensemblePredictor } from "./services/ensemblePredictor";
import { Scheduler } from "./services/scheduler";
import { tradeStore } from "./services/tradeStore";
import { eventBus } from "./core/EventBus";
import { logger } from "./core/Logger";
import { config } from "./core/Config";
import { query } from "./db/postgres";
import { notificationQueue } from "./services/notificationQueue";
import { startPolling } from "./services/telegramService";

import { newsIntelAgent } from "./agents/pipeline/NewsIntelAgent";
import { socialIntelAgent } from "./agents/pipeline/SocialIntelAgent";
import { etfIntelAgent } from "./agents/pipeline/EtfIntelAgent";
import { etfReportAgent } from "./agents/pipeline/EtfReportAgent";
import "./agents/pipeline/EventAnalystAgent";
import "./agents/pipeline/StrategyAgent";
import "./agents/pipeline/ExecutionAgent";
import "./agents/pipeline/AuditAgent";
import "./agents/pipeline/IntelligenceReportAgent";
import "./agents/pipeline/ReportAgent";

const app = express();
const port = process.env.PORT ? parseInt(process.env.PORT, 10) : 3001;
const API_KEY = process.env.INTERNAL_API_KEY || "REMOVED_PRIVATE_VALUE";

app.use(helmet());
app.use(express.json());

const SILENT_ROUTES = ['/api/notifications/pending'];
app.use((req: Request, _res: Response, next: NextFunction) => {
  if (!SILENT_ROUTES.includes(req.path)) {
    logger.info(`[HTTP] ${req.method} ${req.url}`);
  }
  next();
});

const requireKey = (req: Request, res: Response, next: NextFunction) => {
  const key = req.headers["x-api-key"] || req.query.token;
  if (key === API_KEY) return next();
  res.status(401).json({ error: "Unauthorized" });
};

const localOnly = (req: Request, res: Response, next: NextFunction) => {
  const ip = req.socket.remoteAddress;
  if (ip === "127.0.0.1" || ip === "::1" || ip === "::ffff:127.0.0.1") return next();
  res.status(403).json({ error: "Local access only" });
};

const StockSchema = z.object({
  symbol: z.string().min(1).max(10).toUpperCase(),
  name: z.string(),
  price: z.number(),
  history: z.array(z.any()).min(5, "Insufficient price history"),
});

// ─── Startup ────────────────────────────────────────────────────────────────

const startServer = async () => {
  logger.info("[Server] Starting MooPredict V4...");

  try {
    await init_db();
    logger.info("[Server] Database ready.");
  } catch (e: any) {
    logger.error("[Server] Database init failed.", { error: e.message });
  }

  try {
    await axios.get("http://localhost:11434/api/tags", { timeout: 3000 });
    logger.info("[Server] Ollama is ONLINE.");
  } catch {
    logger.error("[Server] Ollama OFFLINE — run 'ollama serve'.");
  }

  try {
    Scheduler.init();
    logger.info("[Server] V4 Scheduler running.");
    
    // Start Telegram Polling
    startPolling();
  } catch (e: any) {
    logger.error("[Server] Scheduler failed.", { error: e.message });
  }

  const server = app.listen(port, "127.0.0.1", () => {
    logger.info(`[Server] ONLINE: http://127.0.0.1:${port}`);
  }).on('error', (err: any) => {
    if (err.code === 'EADDRINUSE') {
      logger.error(`[Server] Port ${port} is already in use. Please run 'npm run preenterprise' to clear it.`);
      process.exit(1);
    } else {
      logger.error(`[Server] Critical startup error:`, err);
    }
  });

  const shutdown = async (signal: string) => {
    logger.info(`[Server] ${signal} — shutting down...`);
    server.close();
    Scheduler.stop();
    setTimeout(() => process.exit(0), 1000);
  };

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
};

// ─── Health ─────────────────────────────────────────────────────────────────

app.get("/api/health/live", (_req: Request, res: Response) => res.status(200).send("OK"));

app.get("/api/health/ready", async (_req: Request, res: Response) => {
  try {
    await init_db();
    res.json({ status: "READY", timestamp: new Date().toISOString() });
  } catch (e: any) {
    res.status(503).json({ status: "NOT_READY", error: e.message });
  }
});

// ─── V4 Status ──────────────────────────────────────────────────────────────

app.get("/api/status", async (_req: Request, res: Response) => {
  try {
    const [portfolio, positions, focus, scheduler] = await Promise.all([
      query("SELECT currency, current_balance, total_pnl, total_pnl_pct FROM virtual_portfolio"),
      tradeStore.getPositions(),
      query("SELECT symbol, direction, catalyst_type, impact_score FROM active_focus WHERE status = 'ACTIVE'"),
      Promise.resolve(Scheduler.getStatus()),
    ]);

    res.json({
      status: "ONLINE",
      mode: "PAPER",
      portfolio: portfolio.rows,
      open_positions: positions.length,
      positions,
      active_focus: focus.rows,
      scheduler: scheduler,
      ollama: await ollamaService.isReady(),
      futu: futu_service.is_connected,
      timestamp: new Date().toISOString(),
    });
  } catch (e: any) {
    res.status(500).json({ status: "ERROR", message: e.message });
  }
});

// ─── Market Data ────────────────────────────────────────────────────────────

app.get("/api/stocks/trending", async (_req: Request, res: Response) => {
  try {
    res.json(await futu_service.get_trending_stocks());
  } catch {
    res.status(500).json({ error: "Failed to fetch trending stocks" });
  }
});

app.get("/api/market/indices", async (_req: Request, res: Response) => {
  try {
    res.json(await futu_service.get_market_indices());
  } catch {
    res.status(500).json({ error: "Failed to fetch market indices" });
  }
});

app.get("/api/stocks/:symbol", async (req: Request, res: Response) => {
  try {
    const { symbol } = req.params;
    const { range = "1M" } = req.query;
    const data = await futu_service.get_stock_data(symbol, range as string);
    res.json(data);
  } catch (e: any) {
    res.status(404).json({ error: "Stock not found", message: e.message });
  }
});

app.get("/api/news/:symbol", async (req: Request, res: Response) => {
  try {
    res.json(await futu_service.get_stock_news(req.params.symbol));
  } catch {
    res.status(500).json({ error: "Failed to fetch news" });
  }
});

// ─── Trading History ─────────────────────────────────────────────────────────

app.get("/api/trading/positions", requireKey, async (_req: Request, res: Response) => {
  res.json(await tradeStore.getPositions());
});

app.get("/api/trading/history", requireKey, async (_req: Request, res: Response) => {
  res.json(await tradeStore.getTradeHistory());
});

// ─── Notifications ───────────────────────────────────────────────────────────

app.get("/api/notifications/pending", (_req: Request, res: Response) => {
  res.json(notificationQueue.getPending());
});

app.post("/api/notifications/mark-sent", (req: Request, res: Response) => {
  const { id } = req.body;
  notificationQueue.markAsSent(id);
  res.json({ success: true });
});

// ─── Prediction (ad-hoc analysis) ────────────────────────────────────────────

app.post("/api/predict", requireKey, async (req: Request, res: Response) => {
  try {
    const stock_data = StockSchema.parse(req.body);
    const prediction = await ensemblePredictor.getPrediction(stock_data as any);
    res.json(prediction);
  } catch (e: any) {
    if (e instanceof z.ZodError) return res.status(400).json({ error: "Validation Error", details: e.issues });
    res.status(500).json({ error: "Prediction failed" });
  }
});

// ─── System Controls ─────────────────────────────────────────────────────────

app.post("/api/system/reset-circuit-breaker", requireKey, (_req: Request, res: Response) => {
  futu_service.getYahooBreaker().reset();
  logger.info("[API] Yahoo circuit breaker reset");
  res.json({ success: true });
});

app.post("/api/system/reboot/:service", requireKey, async (req: Request, res: Response) => {
  const { service } = req.params;
  try {
    if (service === "db") await init_db();
    else if (service === "futu") await futu_service.reconnect();
    else if (service === "sched") { Scheduler.stop(); Scheduler.init(); }
    else if (service === "ai") await axios.get("http://localhost:11434/api/tags", { timeout: 3000 });
    else return res.status(400).json({ error: "Unknown service. Valid: db, futu, sched, ai" });
    res.json({ success: true, service });
  } catch (e: any) {
    res.status(500).json({ success: false, error: e.message });
  }
});

app.post("/api/system/force-scrape", requireKey, async (_req: Request, res: Response) => {
  try {
    logger.info("[API] Manual force-scrape triggered");
    const newsBatch = await newsIntelAgent.scrapeBroadNews();
    const socialBatch = await socialIntelAgent.scrapeSocialIntel();

    eventBus.publish('intel:news_batch', newsBatch);
    eventBus.publish('intel:social_batch', { items: socialBatch });

    res.json({ success: true, message: "Scrape triggered across all agents" });
  } catch (e: any) {
    logger.error("[API] Force-scrape failed:", { error: e.message });
    res.status(500).json({ error: e.message });
  }
});

app.post("/api/system/clear-trades", requireKey, async (req: Request, res: Response) => {
  try {
    if (req.query.confirm !== 'true') {
      return res.status(400).json({ 
        error: 'Confirmation required', 
        message: 'Add ?confirm=true to the URL to permanently delete all positions and proposals.' 
      });
    }
    
    logger.warn("[API] Manual database cleanup triggered (clearing positions and proposals)");
    await query('TRUNCATE positions, trade_proposals RESTART IDENTITY CASCADE');
    res.json({ success: true, message: "Database cleared successfully" });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ─── ETF Module Endpoints ───────────────────────────────────────────────────

app.post("/api/etf/scan", requireKey, async (_req: Request, res: Response) => {
  try {
    logger.info("[API] Manual ETF scan triggered");
    await etfIntelAgent.runAnalysis();
    res.json({ success: true, message: "ETF scan completed" });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

app.get("/api/etf/signals", requireKey, async (_req: Request, res: Response) => {
  try {
    const results = await query(`
      SELECT * FROM etf_signals 
      ORDER BY created_at DESC 
      LIMIT 50
    `);
    res.json(results.rows);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ─── Ollama Proxy (for OpenClaw) ──────────────────────────────────────────────

const ollamaProxy = async (req: Request, res: Response) => {
  try {
    const response = await axios.post("http://localhost:11434/v1/chat/completions", req.body, { timeout: 60000 });
    res.json(response.data);
  } catch (e: any) {
    res.status(500).json({ error: "Ollama proxy error", message: e.message });
  }
};

app.post("/v1/chat/completions", ollamaProxy);
app.post("/api/chat", ollamaProxy);

// ─── Webhook (OpenClaw / Telegram bot commands) ────────────────────────────────

app.post("/webhook", localOnly, async (req: Request, res: Response) => {
  try {
    const command = (req.body?.content || "").trim().toLowerCase();
    logger.info(`[Webhook] Command: ${command}`);

    if (command === "!status" || command === "check") {
      const [portfolio, positions, scheduler] = await Promise.all([
        query("SELECT currency, current_balance, total_pnl FROM virtual_portfolio"),
        tradeStore.getPositions(),
        Promise.resolve(Scheduler.getStatus()),
      ]);

      const usd = portfolio.rows.find((r: any) => r.currency === "USD");

      return res.json({
        content:
          `🤖 MooPredict V4 — PAPER MODE\n` +
          `──────────────────────────\n` +
          `USD Balance: $${parseFloat(usd?.current_balance || 0).toLocaleString()} (PnL: ${parseFloat(usd?.total_pnl || 0) >= 0 ? "+" : ""}${parseFloat(usd?.total_pnl || 0).toFixed(2)})\n` +
          `Open Positions: ${positions.length}/6\n` +
          `Scheduler Tasks: ${scheduler.active} active`,
      });
    }

    if (command === "!positions" || command === "!pos") {
      const positions = await tradeStore.getPositions();
      if (positions.length === 0) return res.json({ content: "No open positions." });
      const lines = positions.map((p: any) => `• ${p.symbol}: ${p.qty} shares @ $${p.entry_price.toFixed(2)}`).join("\n");
      return res.json({ content: `📋 Open Positions (${positions.length}/6):\n${lines}` });
    }

    if (command === "!focus") {
      const focus = await query("SELECT symbol, direction, catalyst_type FROM active_focus WHERE status = 'ACTIVE' ORDER BY added_at DESC");
      if (focus.rows.length === 0) return res.json({ content: "No stocks in active focus." });
      const lines = focus.rows.map((r: any) => `• ${r.symbol} — ${r.direction} (${r.catalyst_type})`).join("\n");
      return res.json({ content: `🎯 Active Focus:\n${lines}` });
    }

    if (command === "!pnl") {
      const portfolio = await query("SELECT currency, total_pnl, total_pnl_pct FROM virtual_portfolio");
      const lines = portfolio.rows.map((r: any) =>
        `${r.currency}: ${parseFloat(r.total_pnl) >= 0 ? "+" : ""}${parseFloat(r.total_pnl).toFixed(2)} (${parseFloat(r.total_pnl_pct).toFixed(2)}%)`
      ).join("\n");
      return res.json({ content: `💰 Paper P&L:\n${lines}` });
    }

    if (command === "!scrape") {
      logger.info("[Webhook] Manual force-scrape triggered via Telegram");
      // Trigger agents
      const newsBatch = await newsIntelAgent.scrapeBroadNews();
      const socialBatch = await socialIntelAgent.scrapeSocialIntel();

      eventBus.publish('intel:news_batch', newsBatch);
      eventBus.publish('intel:social_batch', { items: socialBatch });

      return res.json({ content: "🚀 Scrape triggered! Scanning broad news and social sentiment..." });
    }

    if (command === "!etf") {
      logger.info("[Webhook] Manual ETF scan triggered via Telegram");
      await etfIntelAgent.runAnalysis();
      return res.json({ content: "📡 ETF Analysis triggered! Checking sector rotations and relative strength..." });
    }

    if (command === "!testbuy") {
      logger.info("[Webhook] Manual testbuy triggered via Telegram");
      // Bypassing StrategyAgent data fetch to ensure the test works regardless of market data availability
      const testPlan = {
        correlation_id: uuidv4(),
        symbol: 'NVDA',
        action: 'BUY' as const,
        entry_price: 138.50, // Realistic test price
        best_target: 155.00,
        safe_target: 145.00,
        stop_loss: 132.00,
        max_hold_days: 5,
        confidence: 0.95,
        reasoning: 'System test trigger to verify Paper Mode execution pipeline.',
        catalyst_id: null,
        timestamp: new Date()
      };

      // Publish to ExecutionAgent
      eventBus.publish('strategy:trade_plan', testPlan);
      
      return res.json({ content: "🧪 Pipeline Test triggered! A Paper BUY for NVDA should execute immediately. Check Telegram!" });
    }

    if (command === "!sync") {
      logger.info("[Webhook] Manual watchlist sync triggered via Telegram");
      (async () => {
        try {
          const symbols = await futu_service.get_user_watchlist();
          if (symbols.length > 0) {
            await query("DELETE FROM user_watchlist");
            for (const sym of symbols) {
              await query("INSERT INTO user_watchlist (symbol) VALUES ($1) ON CONFLICT DO NOTHING", [sym]);
            }
            const { sendTelegramMessage } = await import("./services/telegramService");
            await sendTelegramMessage(`✅ Watchlist sync complete! Captured ${symbols.length} symbols.`);
          }
        } catch (e: any) {
          logger.error("[Webhook] Sync failed:", { error: e.message });
        }
      })();
      return res.json({ content: "🔄 Syncing watchlist from Moomoo... This may take a moment." });
    }

    if (command === "!syncpos") {
      logger.info("[Webhook] Manual position sync triggered via Telegram");
      (async () => {
        try {
          const positions = await futu_service.get_real_positions();
          const { sendTelegramMessage } = await import("./services/telegramService");
          if (positions.length === 0) {
            await sendTelegramMessage("📭 No open positions found.");
            return;
          }
          const lines = positions.map((p: any) => {
            const pnlSign = p.pnl >= 0 ? '+' : '';
            const pnlPct = typeof p.pnl_pct === 'number' ? ` (${pnlSign}${(p.pnl_pct * 100).toFixed(2)}%)` : '';
            return `• ${p.symbol} [${p.market}]: ${p.qty} shares @ $${Number(p.avg_price).toFixed(2)} | P&L: ${pnlSign}${Number(p.pnl).toFixed(2)}${pnlPct}`;
          }).join('\n');
          await sendTelegramMessage(`📊 *Real Positions (${positions.length}):*\n${lines}`);
        } catch (e: any) {
          logger.error("[Webhook] Position sync failed:", { error: e.message });
        }
      })();
      return res.json({ content: "⏳ Fetching live positions from Moomoo... One moment." });
    }

    if (command === "!checkstatus") {
      logger.info("[Webhook] !checkstatus triggered via Telegram");
      (async () => {
        const { sendTelegramMessage } = await import("./services/telegramService");
        try {
          const [positions, balance] = await Promise.all([
            futu_service.get_real_positions(),
            futu_service.get_account_balance(),
          ]);

          const now = new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
          const totalUnrealized = positions.reduce((sum: number, p: any) => sum + Number(p.pnl || 0), 0);
          const totalSign = totalUnrealized >= 0 ? "+" : "";

          let msg = `📊 *Live Account Status*\n`;
          msg += `━━━━━━━━━━━━━━━━━━━━\n`;
          msg += `💼 *Balance (${balance.currency})*\n`;
          msg += `Total Assets: $${Number(balance.total_assets).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}\n`;
          msg += `Available Cash: $${Number(balance.available_cash).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}\n\n`;

          if (positions.length === 0) {
            msg += `📭 *No open positions*\n`;
          } else {
            msg += `📈 *Open Positions (${positions.length})*\n`;
            for (const p of positions) {
              const pnl = Number(p.pnl || 0);
              const pnlPct = typeof p.pnl_pct === "number" ? p.pnl_pct * 100 : 0;
              const sign = pnl >= 0 ? "+" : "";
              const icon = pnl >= 0 ? "✅" : "🔴";
              msg += `\n▸ *${p.symbol}* [${p.market}]\n`;
              msg += `  Qty: ${p.qty} | Avg: $${Number(p.avg_price).toFixed(2)} | Now: $${Number(p.current_price).toFixed(2)}\n`;
              msg += `  P&L: ${sign}$${pnl.toFixed(2)} (${sign}${pnlPct.toFixed(2)}%) ${icon}\n`;
            }
            msg += `\n━━━━━━━━━━━━━━━━━━━━\n`;
            msg += `📊 Total Unrealized: ${totalSign}$${totalUnrealized.toFixed(2)}\n`;
          }

          msg += `🕐 Updated: ${now}`;
          await sendTelegramMessage(msg);
        } catch (e: any) {
          logger.error("[Webhook] !checkstatus failed:", { error: e.message });
          const { sendTelegramMessage } = await import("./services/telegramService");
          await sendTelegramMessage("⚠️ Could not fetch status. Check that Moomoo OpenD is running.");
        }
      })();
      return res.json({ content: "⏳ Fetching live status from Moomoo..." });
    }

    if (command === "!accounts") {
      const accs = futu_service.get_all_accounts();
      const lines = accs.map((a: any) => 
        `• ID: ${a.accID} | Env: ${a.trdEnv === 1 ? 'REAL' : 'SIMULATE'} | Markets: ${a.trdMarketAuthList.join(',')}`
      ).join('\n');
      return res.json({ content: `🏦 *Discovered Accounts:* \n${lines}\n\n*Note:* If your Universal Account (3378) is not listed, please check your FutuOpenD login.` });
    }

    res.json({ content: "Commands: !checkstatus, !status, !positions, !syncpos, !accounts, !focus, !pnl, !scrape, !etf, !testbuy, !sync" });
  } catch (e: any) {
    logger.error("[Webhook] Error:", { error: e.message });
    res.status(500).json({ error: "Webhook error" });
  }
});

// ─── Start ────────────────────────────────────────────────────────────────────

startServer();
