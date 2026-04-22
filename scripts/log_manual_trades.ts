import { tradeStore } from "../server/services/tradeStore";

async function logManualTrades() {
  console.log("--- Logging Manual Trades for US Market ---");
  
  const trades = [
    { symbol: 'AMZN', price: 252.72, budget: 45 },
    { symbol: 'PYPL', price: 51.73, budget: 45 },
    { symbol: 'NFLX', price: 94.17, budget: 45 }
  ];

  for (const t of trades) {
    const qty = parseFloat((t.budget / t.price).toFixed(4));
    
    // 1. Add to active positions
    await tradeStore.addPosition({
      symbol: t.symbol,
      mode: 'LIVE', // Setting as LIVE so monitor watches it
      qty,
      entry_price: t.price,
      side: 'LONG',
      stop_loss_pct: -5,
      take_profit_pct: 10
    });

    // 2. Add to trade history
    await tradeStore.addTradeRecord({
      symbol: t.symbol,
      mode: 'LIVE',
      side: 'BUY',
      qty,
      price: t.price,
      reason: 'Manual Buy Logged via Assistant'
    });

    console.log(`✅ Logged ${t.symbol}: ${qty} shares @ $${t.price}`);
  }
}

logManualTrades().catch(console.error);
