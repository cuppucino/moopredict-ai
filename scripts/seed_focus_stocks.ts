import { query } from "../server/db/database";

async function seed() {
  const stocks = ['MSFT', 'AAPL', 'NVDA', '700', '9988', '3690'];
  console.log("🌱 Seeding focus stocks:", stocks);
  
  for (const symbol of stocks) {
    try {
      await query('INSERT INTO watchlist (symbol) VALUES ($1) ON CONFLICT DO NOTHING', [symbol]);
      console.log(`✅ Added ${symbol}`);
    } catch (e) {
      console.error(`❌ Failed to add ${symbol}:`, e);
    }
  }
  process.exit(0);
}

seed();
