import { query } from "../db/database";

export interface Bot {
  symbol: string;
  mode: "PAPER" | "LIVE" | "PAPER_EXPLORE";
  status: "RUNNING" | "PAUSED" | "STOPPED";
}

export interface Position {
  id?: number;
  symbol: string;
  mode: "PAPER" | "LIVE" | "PAPER_EXPLORE";
  qty: number;
  entry_price: number;
  side: "LONG";
  stop_loss_pct: number;
  take_profit_pct: number;
}

export interface TradeRecord {
  id?: number;
  symbol: string;
  mode: "PAPER" | "LIVE" | "PAPER_EXPLORE" | "VIRTUAL_SKIP";
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  reason: string;
  pnl?: number;
  created_at?: string;
}

class TradeStore {
  // Bots
  async getBots(): Promise<Bot[]> {
    const result = await query("SELECT * FROM bots");
    return result.rows as Bot[];
  }

  async getBot(symbol: string): Promise<Bot | undefined> {
    const result = await query("SELECT * FROM bots WHERE symbol = $1", [symbol]);
    return result.rows[0] as Bot | undefined;
  }

  async upsertBot(bot: Bot): Promise<void> {
    await query(`
      INSERT INTO bots (symbol, mode, status, updated_at) 
      VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
      ON CONFLICT(symbol, mode) DO UPDATE SET 
        status = EXCLUDED.status,
        updated_at = CURRENT_TIMESTAMP
    `, [bot.symbol, bot.mode, bot.status]);
  }

  async deleteBot(symbol: string): Promise<void> {
    await query("DELETE FROM bots WHERE symbol = $1", [symbol]);
  }

  // Positions
  async getPositions(): Promise<Position[]> {
    const result = await query("SELECT * FROM positions");
    return result.rows as Position[];
  }

  async getPosition(symbol: string, mode: "PAPER" | "LIVE" | "PAPER_EXPLORE"): Promise<Position | undefined> {
    const result = await query("SELECT * FROM positions WHERE symbol = $1 AND mode = $2", [symbol, mode]);
    return result.rows[0] as Position | undefined;
  }

  async addPosition(pos: Position): Promise<void> {
    await query(`
      INSERT INTO positions (symbol, mode, qty, entry_price, side, stop_loss_pct, take_profit_pct)
      VALUES ($1, $2, $3, $4, $5, $6, $7)
    `, [pos.symbol, pos.mode, pos.qty, pos.entry_price, pos.side, pos.stop_loss_pct, pos.take_profit_pct]);
  }

  async removePosition(symbol: string, mode: "PAPER" | "LIVE" | "PAPER_EXPLORE"): Promise<void> {
    await query("DELETE FROM positions WHERE symbol = $1 AND mode = $2", [symbol, mode]);
  }

  // Trade History
  async addTradeRecord(trade: TradeRecord): Promise<void> {
    await query(`
      INSERT INTO trade_history (symbol, mode, side, qty, price, reason, pnl)
      VALUES ($1, $2, $3, $4, $5, $6, $7)
    `, [trade.symbol, trade.mode, trade.side, trade.qty, trade.price, trade.reason, trade.pnl ?? null]);
  }

  async getTradeHistory(): Promise<TradeRecord[]> {
    const result = await query("SELECT * FROM trade_history ORDER BY created_at DESC LIMIT 100");
    return result.rows as TradeRecord[];
  }

  // Daily PnL
  async getTodayPnL(): Promise<number> {
    const today = new Date().toISOString().split("T")[0];
    const result = await query("SELECT total_pnl FROM daily_pnl WHERE date = $1", [today]);
    const row = result.rows[0] as { total_pnl: number } | undefined;
    return row ? parseFloat(row.total_pnl as any) : 0;
  }

  async addDailyPnL(amount: number): Promise<void> {
    const today = new Date().toISOString().split("T")[0];
    await query(`
      INSERT INTO daily_pnl (date, total_pnl) VALUES ($1, $2)
      ON CONFLICT(date) DO UPDATE SET 
        total_pnl = daily_pnl.total_pnl + EXCLUDED.total_pnl,
        updated_at = CURRENT_TIMESTAMP
    `, [today, amount]);
  }


  async saveAgentLearning(data: {
    date: string;
    total_gain: number;
    total_loss: number;
    lesson: string;
    top_performer: string;
    worst_performer: string;
  }): Promise<void> {
    await query(`
      INSERT INTO agent_learnings (date, market, total_gain, total_loss, lesson, top_performer, worst_performer)
      VALUES ($1, 'US', $2, $3, $4, $5, $6)
      ON CONFLICT(date) DO UPDATE SET
        total_gain = EXCLUDED.total_gain,
        total_loss = EXCLUDED.total_loss,
        lesson = EXCLUDED.lesson,
        top_performer = EXCLUDED.top_performer,
        worst_performer = EXCLUDED.worst_performer
    `, [data.date, data.total_gain, data.total_loss, data.lesson, data.top_performer, data.worst_performer]);
  }
}

export const tradeStore = new TradeStore();
