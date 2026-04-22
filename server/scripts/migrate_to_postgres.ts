import { query } from "../db/database";
import * as fs from 'fs';
import * as path from 'path';

async function migrate() {
    console.log("🚀 Starting SQLite to PostgreSQL migration...");

    // 1. Migrate Watchlist
    const watchlistPath = path.join(__dirname, '../data/watchlist.json');
    if (fs.existsSync(watchlistPath)) {
        const watchlist = JSON.parse(fs.readFileSync(watchlistPath, 'utf8'));
        for (const item of watchlist) {
            await query(
                'INSERT INTO watchlist (symbol, name, added_at) VALUES ($1, $2, $3) ON CONFLICT (symbol) DO NOTHING',
                [item.symbol, item.name, item.addedAt]
            );
        }
        console.log(`✅ Migrated ${watchlist.length} stocks to watchlist`);
    }

    // 2. Migrate Bots
    const botsPath = path.join(__dirname, '../data/bots.json');
    if (fs.existsSync(botsPath)) {
        const bots = JSON.parse(fs.readFileSync(botsPath, 'utf8'));
        for (const bot of bots) {
            await query(
                'INSERT INTO bots (symbol, mode, status, settings) VALUES ($1, $2, $3, $4) ON CONFLICT (symbol) DO UPDATE SET mode = $2, status = $3, settings = $4',
                [bot.symbol, bot.mode, bot.status, JSON.stringify(bot.settings)]
            );
        }
        console.log(`✅ Migrated ${bots.length} trading bots`);
    }

    // 3. Migrate Positions
    const positionsPath = path.join(__dirname, '../data/positions.json');
    if (fs.existsSync(positionsPath)) {
        const positions = JSON.parse(fs.readFileSync(positionsPath, 'utf8'));
        for (const pos of positions) {
            await query(
                'INSERT INTO positions (symbol, qty, entry_price, current_price, pnl, pnl_pct) VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (symbol) DO UPDATE SET qty = $2, entry_price = $3',
                [pos.symbol, pos.qty, pos.entry_price, pos.current_price, pos.pnl, pos.pnl_pct]
            );
        }
        console.log(`✅ Migrated ${positions.length} active positions`);
    }

    // 4. Migrate Trade History
    const historyPath = path.join(__dirname, '../data/history.json');
    if (fs.existsSync(historyPath)) {
        const history = JSON.parse(fs.readFileSync(historyPath, 'utf8'));
        for (const trade of history) {
            await query(
                'INSERT INTO trade_history (symbol, mode, side, qty, price, pnl, reason, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)',
                [trade.symbol, trade.mode, trade.side, trade.qty, trade.price, trade.pnl, trade.reason, trade.created_at]
            );
        }
        console.log(`✅ Migrated ${history.length} trades to history`);
    }

    // 5. Migrate Daily PnL
    const pnlPath = path.join(__dirname, '../data/daily_pnl.json');
    if (fs.existsSync(pnlPath)) {
        const pnls = JSON.parse(fs.readFileSync(pnlPath, 'utf8'));
        for (const pnl of pnls) {
            await query(
                'INSERT INTO daily_pnl (date, total_pnl, trade_count, win_rate) VALUES ($1, $2, $3, $4) ON CONFLICT (date) DO NOTHING',
                [pnl.date, pnl.total_pnl, pnl.trade_count, pnl.win_rate]
            );
        }
        console.log(`✅ Migrated Daily PnL records`);
    }

    console.log("🏁 Migration Complete!");
}

migrate().catch(console.error);
