import { marketHoursService } from "./marketHoursService";

async function testMarketHours() {
  console.log("--- Testing MarketHoursService ---");
  
  const symbols = ['AAPL', '700', 'NVDA', '00700.HK'];
  
  for (const symbol of symbols) {
    const status = marketHoursService.isMarketOpen(symbol);
    console.log(`Symbol: ${symbol.padEnd(8)} | Market: ${status.market} | Open: ${status.isOpen ? '✅ YES' : '❌ NO'} | Reason: ${status.reason}`);
  }

  // Double check US RTH logic with forced UTC mocks?
  // Current UTC time:
  const now = new Date();
  console.log(`Current UTC: ${now.getUTCHours()}:${now.getUTCMinutes()}`);
}

testMarketHours().catch(console.error);
