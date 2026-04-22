import { StockData } from "./aiService";
import { createRequire } from "module";
import { logger } from "../core/Logger";
import { config } from "../core/Config";
import { CircuitBreaker } from "../core/CircuitBreaker";

// createRequire allows loading CJS-only packages from ESM context
const _require = createRequire(import.meta.url);
const futuApi = _require("moomoo-api");
const protoObj = _require("moomoo-api/proto.js");

const ftapi = futuApi.default || futuApi;
const QotMarket = protoObj.Qot_Common?.nested?.QotMarket?.values || {};
const KLType = protoObj.Qot_Common?.nested?.KLType?.values || {};
const RehabType = protoObj.Qot_Common?.nested?.RehabType?.values || {};

// Trading Constants - proto values: Simulate=0, Real=1
const TrdEnv = protoObj.Trd_Common?.nested?.TrdEnv?.values || { TrdEnv_Simulate: 0, TrdEnv_Real: 1 };
const TrdMarket = protoObj.Trd_Common?.nested?.TrdMarket?.values || { TrdMarket_HK: 1, TrdMarket_US: 2 };
const TrdSecMarket = protoObj.Trd_Common?.nested?.TrdSecMarket?.values || { TrdSecMarket_HK: 1, TrdSecMarket_US: 2 };
const TrdSide = protoObj.Trd_Common?.nested?.TrdSide?.values || { TrdSide_Buy: 1, TrdSide_Sell: 2 };
const OrderType = protoObj.Trd_Common?.nested?.OrderType?.values || { OrderType_Normal: 1, OrderType_Market: 2 };

// yahoo-finance2 v2 exports a singleton, not a class
const yahooFinance = _require("yahoo-finance2").default;

export interface MarketIndex {
  name: string;
  symbol: string;
  value: number;
  change_percent: number;
}

export interface NewsItem {
  title: string;
  time: string;
  sentiment: "Positive" | "Negative" | "Neutral";
  url?: string;
}

export class FutuService {
  private client: any;
  public is_connected: boolean = false;
  private is_connecting: boolean = false;
  private acc_id: number = 0;
  private futuBreaker = new CircuitBreaker({ name: 'FutuOpenD', failureThreshold: 3, resetTimeoutMs: 60000 });
  private yahooBreaker = new CircuitBreaker({ name: 'YahooFinance', failureThreshold: 5, resetTimeoutMs: 30000 });

  constructor() {
    this.try_connect_futu();
  }

  /** resolve the correct TrdEnv enum value */
  private get_trd_env(): number {
    return config.FUTU_TRADE_MODE === 'LIVE' ? TrdEnv.TrdEnv_Real : TrdEnv.TrdEnv_Simulate;
  }

  /** resolve the correct TrdMarket enum value based on symbol */
  private get_trd_market(symbol: string): number {
    const isHK = /^\d+$/.test(symbol) || symbol.toUpperCase().endsWith('.HK');
    return isHK ? TrdMarket.TrdMarket_HK : TrdMarket.TrdMarket_US;
  }

  /** resolve the correct TrdSecMarket enum value for PlaceOrder based on symbol */
  private get_trd_sec_market(symbol: string): number {
    const isHK = /^\d+$/.test(symbol) || symbol.toUpperCase().endsWith('.HK');
    return isHK ? TrdSecMarket.TrdSecMarket_HK : TrdSecMarket.TrdSecMarket_US;
  }


  public getYahooBreaker() {
    return this.yahooBreaker;
  }

  public async reconnect(): Promise<void> {
    logger.info("[FutuService] Manual reconnection requested...");
    if (this.client) {
      try { this.client.stop(); } catch(e) {}
    }
    this.is_connected = false;
    await this.try_connect_futu();
  }

  private async try_connect_futu(): Promise<void> {
    if (this.is_connecting || this.is_connected) return;

    try {
      this.is_connecting = true;
      logger.info("[FutuService] Attempting to connect to FutuOpenD...");
      
      // Cleanup old client if it exists
      if (this.client) {
        try { this.client.stop(); } catch (e) {}
        this.client = null;
      }

      this.client = new ftapi();
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("Timeout connecting to FutuOpenD")), 15000);
        
        this.client.onlogin = (success: boolean, response: any) => {
          clearTimeout(timeout);
          if (success) {
            this.is_connected = true;
            this.is_connecting = false;
            logger.info("[FutuService] Successfully connected to FutuOpenD", { host: config.FUTU_HOST, port: config.FUTU_PORT });
            resolve();
          } else {
            const msg = response?.retMsg || response?.error || "Unknown login error";
            reject(new Error(`Futu login failed: ${msg}`));
          }
        };

        this.client.start(config.FUTU_HOST, config.FUTU_PORT, false);
      });

      // After successful connection, discover account ID
      await this.discover_account();
    } catch (error: any) {
      this.is_connected = false;
      this.is_connecting = false;
      logger.warn(`[FutuService] FutuOpenD unavailable: ${error.message}. Fallback mode active.`);
      
      // Auto-reconnect attempt (increased to 2 minutes to reduce noise)
      setTimeout(() => {
        if (!this.is_connected && !this.is_connecting) {
          logger.info("[FutuService] Retrying FutuOpenD connection...");
          this.try_connect_futu();
        }
      }, 120000); 
    }
  }

  /** extract a plain number from a protobuf Long or number */
  private to_number(val: any): number {
    if (typeof val === 'number') return val;
    if (val && typeof val.toNumber === 'function') return val.toNumber();
    if (val && typeof val.low === 'number') return val.low;
    return Number(val) || 0;
  }

  /** discover the correct accID from GetAccList */
  private async discover_account(): Promise<void> {
    try {
      const trd_env = this.get_trd_env();
      // Use config.TRADE_MARKET for discovery context
      const preferred_market = config.TRADE_MARKET === 'US' ? TrdMarket.TrdMarket_US : TrdMarket.TrdMarket_HK;
      const res: any = await this.client.GetAccList({ c2s: { userID: 0 } });


      if (res.retType !== 0) {
        logger.error("[FutuService] GetAccList failed", { retMsg: res.retMsg });
        return;
      }

      const acc_list: any[] = res.s2c?.accList || [];
      logger.info(`[FutuService] Found ${acc_list.length} trading accounts`, {
        accounts: acc_list.map((a: any) => ({ id: this.to_number(a.accID), env: a.trdEnv, markets: a.trdMarketAuthList }))
      });

      // find an account matching our desired env and market
      const matched = acc_list.find((a: any) =>
        a.trdEnv === trd_env && (a.trdMarketAuthList || []).includes(preferred_market)
      );


      if (matched) {
        this.acc_id = this.to_number(matched.accID);
        logger.info(`[FutuService] Using account`, { accID: this.acc_id, env: config.FUTU_TRADE_MODE, market: config.TRADE_MARKET });
      } else {
        // fallback: use the first account that matches our env
        const fallback = acc_list.find((a: any) => a.trdEnv === trd_env) || acc_list[0];
        if (fallback) {
          this.acc_id = this.to_number(fallback.accID);
          logger.warn(`[FutuService] No exact market match, using fallback account`, { accID: this.acc_id });
        } else {
          logger.error("[FutuService] No trading accounts found!");
        }
      }
    } catch (error: any) {
      logger.error("[FutuService] Account discovery failed", { error: error.message });
    }
  }

  /**
   * --- Order Execution ---
   */

  async get_board_lot(symbol: string): Promise<number> {
    if (!this.is_connected) return 100;

    return this.futuBreaker.execute(async () => {
      try {
        const clean_symbol = symbol.toUpperCase();
        let futu_symbol = clean_symbol.split('.')[0];
        let market = QotMarket.QotMarket_US_Security;

        // Market resolution logic (simplified from fetch_from_futu)
        if (clean_symbol.endsWith('.HK') || /^\d{5}$/.test(clean_symbol)) {
          market = QotMarket.QotMarket_HK_Security;
        } else if (clean_symbol.endsWith('.MY') || /^\d{4}$/.test(clean_symbol)) {
          market = QotMarket.QotMarket_MY_Security;
        } else if (clean_symbol.endsWith('.SI') || clean_symbol.endsWith('.SG')) {
          market = QotMarket.QotMarket_SG_Security;
        }

        const res: any = await this.client.GetSecuritySnapshot({ 
          c2s: { securityList: [{ market, code: futu_symbol }] } 
        });

        const lotSize = res?.s2c?.snapshotList?.[0]?.basic?.lotSize;
        if (lotSize) {
          logger.info(`[FutuService] Detected board lot for ${symbol}: ${lotSize}`);
          return lotSize;
        }
        
        return 100; // US stocks and safe default
      } catch (error) {
        logger.warn(`[FutuService] Could not fetch board lot for ${symbol}, falling back to 100`);
        return 100;
      }
    });
  }

  async place_order(symbol: string, side: 'BUY' | 'SELL', qty: number, price?: number): Promise<{ success: boolean; order_id?: string; error?: string }> {
    if (!this.is_connected) {
      return { success: false, error: "FutuOpenD not connected" };
    }

    return this.futuBreaker.execute(async () => {
      const clean_symbol = symbol.toUpperCase().split('.')[0];
      
      logger.info(`[FutuService] Placing ${side} order for ${qty} ${symbol}`, { env: config.FUTU_TRADE_MODE, price, accID: this.acc_id });

      try {
        const raw_conn_id = this.client.getConnID?.(); // returns protobuf Long — pass as-is
        const res: any = await this.client.PlaceOrder({
          c2s: {
            packetID: {
              connID: raw_conn_id || 0,
              serialNo: Math.floor(Math.random() * 1000000),
            },
            header: {
              trdEnv: this.get_trd_env(),
              trdMarket: this.get_trd_market(symbol),
              accID: this.acc_id,
            },
            trdSide: side === 'BUY' ? TrdSide.TrdSide_Buy : TrdSide.TrdSide_Sell,
            orderType: price ? OrderType.OrderType_Normal : OrderType.OrderType_Market,
            code: clean_symbol,
            qty,
            price: price || 0,
            adjustPrice: true,
            secMarket: this.get_trd_sec_market(symbol),
          }

        });

        logger.info("[FutuService] PlaceOrder raw response", { retType: res.retType, retMsg: res.retMsg, s2c: JSON.stringify(res.s2c) });

        const ret_type = this.to_number(res.retType);
        if (ret_type !== 0) {
          logger.error(`[FutuService] Order failed: ${res.retMsg}`, { symbol, side, qty });
          return { success: false, error: res.retMsg };
        }

        const order_id = String(this.to_number(res.s2c?.orderID));
        logger.info(`[FutuService] Order placed successfully`, { order_id, symbol, side });
        return { success: true, order_id };
      } catch (err: any) {
        let errStr = String(err);
        try { errStr = JSON.stringify(err); } catch(e){}
        logger.error("[FutuService] PlaceOrder exception", { message: err?.message, stack: err?.stack, raw: errStr });
        throw err;
      }
    });
  }

  async get_account_balance(): Promise<{ total_assets: number; available_cash: number; currency: string }> {
    if (!this.is_connected) {
      return { total_assets: 0, available_cash: 0, currency: config.TRADE_CURRENCY };
    }

    return this.futuBreaker.execute(async () => {
      const res: any = await this.client.GetFunds({
        c2s: {
          header: {
            trdEnv: this.get_trd_env(),
            trdMarket: config.TRADE_MARKET === 'US' ? TrdMarket.TrdMarket_US : TrdMarket.TrdMarket_HK,
            accID: this.acc_id,
          }
        }
      });


      logger.debug("[FutuService] GetFunds response", { retType: res.retType, retMsg: res.retMsg, s2c: res.s2c });

      if (res.retType !== 0) throw new Error(res.retMsg || `GetFunds failed with retType=${res.retType}`);

      const funds = res.s2c?.funds;
      return {
        total_assets: this.to_number(funds?.totalAssets),
        available_cash: this.to_number(funds?.cash),
        currency: config.TRADE_CURRENCY
      };
    });
  }

  async get_real_positions(): Promise<any[]> {
    if (!this.is_connected) return [];

    return this.futuBreaker.execute(async () => {
      const res: any = await this.client.GetPositionList({
        c2s: {
          header: {
            trdEnv: this.get_trd_env(),
            trdMarket: config.TRADE_MARKET === 'US' ? TrdMarket.TrdMarket_US : TrdMarket.TrdMarket_HK,
            accID: this.acc_id,
          }
        }
      });


      if (res.retType !== 0) throw new Error(res.retMsg || `GetPositionList failed with retType=${res.retType}`);

      return (res.s2c?.positionList || []).map((p: any) => ({
        symbol: p.code,
        qty: p.qty,
        avg_price: p.costPrice,
        current_price: p.canSellQty > 0 ? p.costPrice : 0,
        pnl: p.plVal
      }));
    });
  }

  /**
   * --- Market Data ---
   */

  async get_stock_data(symbol: string, range: string = '1M'): Promise<StockData> {
    const safeRange = range || '1M';
    if (this.is_connected) {
      try {
        return await this.futuBreaker.execute(() => this.fetch_from_futu(symbol, safeRange));
      } catch (error) {
        logger.warn(`[FutuService] Futu fetch failed for ${symbol}, falling back to Yahoo Finance`);
      }
    }
    const normalized_symbol = this.normalize_yahoo_symbol(symbol);
    return this.yahooBreaker.execute(() => this.fetch_from_yahoo(normalized_symbol, safeRange));
  }

  /** Normalizes symbols for Yahoo Finance (e.g., 700 -> 0700.HK, 00700 -> 0700.HK) */
  private normalize_yahoo_symbol(symbol: string): string {
    const clean = symbol.toUpperCase().trim();
    if (clean.includes('.')) return clean; // Already hyphenated/suffixed (e.g. AAPL, 0700.HK)

    // Numeric symbols (likely HK stocks)
    if (/^\d+$/.test(clean)) {
      // If it's a numeric symbol with 1-4 digits, pad to 4 and add .HK
      if (clean.length <= 4) {
        return `${clean.padStart(4, '0')}.HK`;
      }
      // If it's a numeric symbol with 5 digits (some HK stocks like 9988)
      if (clean.length === 5) {
        // If it starts with 0 and followed by 4 digits, normalize to 4 digits (common for 00700)
        if (clean.startsWith('0')) {
          return `${clean.substring(1)}.HK`;
        }
        return `${clean}.HK`;
      }
    }

    return clean; // Default to US or other
  }

  private async fetch_from_futu(symbol: string, range: string): Promise<StockData> {
    const clean_symbol = symbol.toUpperCase();
    let futu_symbol = clean_symbol.split('.')[0];
    // Default to US — if no suffix and alphabet-only, likely US
    let market = QotMarket.QotMarket_US_Security;

    if (clean_symbol.endsWith('.KL') || clean_symbol.endsWith('.MY')) {
      market = QotMarket.QotMarket_MY_Security;
    } else if (clean_symbol.endsWith('.HK')) {
      market = QotMarket.QotMarket_HK_Security;
    } else if (clean_symbol.endsWith('.SI') || clean_symbol.endsWith('.SG')) {
      market = QotMarket.QotMarket_SG_Security;
    } else if (clean_symbol.endsWith('.US')) {
      market = QotMarket.QotMarket_US_Security;
    } else if (/^\d{4}$/.test(clean_symbol)) {
      // 4 digit numbers are usually Malaysia stocks in this region
      market = QotMarket.QotMarket_MY_Security;
    } else if (/^\d{5}$/.test(clean_symbol)) {
      // 5 digit numbers are usually Hong Kong stocks
      market = QotMarket.QotMarket_HK_Security;
    }

    const security = { market, code: futu_symbol };
    const res: any = await this.client.GetSecuritySnapshot({ 
      c2s: { securityList: [security] } 
    });

    if (!res.s2c?.snapshotList?.length) {
      throw new Error("Snapshot not found");
    }

    const snapshot = res.s2c.snapshotList[0];
    console.log("[FutuService Debug] Raw Snapshot Basic:", JSON.stringify(snapshot.basic));
    const q = snapshot.basic;

    // Get history
    // History logic based on range
    let klType = KLType.KLType_Day;
    let reqNum = 100;

    const normalizedRange = (range || '1M').toUpperCase().trim();
    logger.info(`[FutuService] Processing range: "${normalizedRange}"`);

    switch (normalizedRange) {
      case '1D':
        klType = KLType.KLType_5Min;
        reqNum = 100; // ~8 hours of trading coverage
        break;
      case '1W':
        klType = KLType.KLType_Day;
        reqNum = 7;
        break;
      case '1M':
        klType = KLType.KLType_Day;
        reqNum = 30;
        break;
      case '1Y':
        klType = KLType.KLType_Day;
        reqNum = 252;
        break;
      case 'ALL':
        klType = KLType.KLType_Day;
        reqNum = 1000;
        break;
      default:
        console.warn(`[FutuService] Unknown range "${range}", defaulting to 1M`);
        klType = KLType.KLType_Day;
        reqNum = 30;
        break;
    }

    const kl_res: any = await this.client.GetKL({ 
      c2s: { 
        security, 
        rehabType: RehabType.RehabType_Forward, 
        klType, 
        reqNum 
      }
    });

    const klList = kl_res.s2c?.klList || [];
    console.log(`[FutuService] Received ${klList.length} points from Futu for range: ${normalizedRange}`);
    
    if (klList.length === 0) {
      throw new Error("Empty history from Futu (likely missing market data subscription)");
    }

    const history = klList.map((kl: any) => ({
      date: normalizedRange === '1D' ? kl.time : (kl.time?.split(" ")[0] || ""),
      price: parseFloat(kl.closePrice ?? 0),
      open: parseFloat(kl.openPrice ?? 0),
      high: parseFloat(kl.highPrice ?? 0),
      low: parseFloat(kl.lowPrice ?? 0),
      volume: kl.volume ?? 0,
    }));

    const market_names: Record<number, string> = {
      [QotMarket.QotMarket_US_Security]: "NASDAQ/NYSE",
      [QotMarket.QotMarket_HK_Security]: "HKEX",
      [QotMarket.QotMarket_SG_Security]: "SGX",
      [QotMarket.QotMarket_MY_Security]: "Bursa Malaysia",
    };

    return {
      symbol: symbol.toUpperCase(),
      name: snapshot.title || symbol,
      price: q.curPrice ?? 0,
      change: (q.curPrice ?? 0) - (q.lastClosePrice ?? 0),
      changePercent: parseFloat((((q.curPrice ?? 0) - (q.lastClosePrice ?? 0)) / (q.lastClosePrice || 1) * 100).toFixed(2)),
      high: q.highPrice ?? 0,
      low: q.lowPrice ?? 0,
      volume: q.volume ?? 0,
      marketCap: "Via Futu",
      market: market_names[market] || "Unknown",
      peRatio: q.peRatio ?? 0,
      history,
    };
  }

  private async fetch_from_yahoo(symbol: string, range: string): Promise<StockData> {
    try {
      const quote = await yahooFinance.quote(symbol);
      if (!quote) throw new Error(`Yahoo returned no data for ${symbol}`);
      let historical: any[] = [];

      const normalizedRange = (range || '1M').toUpperCase().trim();
      let period1: Date;
      let interval: "1m" | "2m" | "5m" | "15m" | "30m" | "60m" | "90m" | "1h" | "1d" | "5d" | "1wk" | "1mo" | "3mo" = "1d";

      switch (normalizedRange) {
        case '1D':
          period1 = new Date(Date.now() - 24 * 60 * 60 * 1000); // last 24h
          interval = "5m";
          break;
        case '1W':
          period1 = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
          interval = "1d";
          break;
        case '1M':
          period1 = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
          interval = "1d";
          break;
        case '1Y':
          period1 = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000);
          interval = "1d";
          break;
        case 'ALL':
          period1 = new Date(Date.now() - 10 * 365 * 24 * 60 * 60 * 1000); // 10 years
          interval = "1d";
          break;
        default:
          period1 = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
          interval = "1d";
      }
      
      const p1 = Math.floor(period1.getTime() / 1000);
      const p2 = Math.floor(Date.now() / 1000);
      
      logger.info(`[FutuService] Yahoo fetch: range=${normalizedRange}, interval=${interval}, p1=${p1}, p2=${p2}`);
      
      try {
        const chart_res = await yahooFinance.chart(symbol, {
          period1: period1,
          period2: new Date(),
          interval,
        });
        const historical_raw = chart_res.quotes || [];
        
        // Filter out nulls but be smart about today's price
        historical = historical_raw.map((h: any) => {
          if (h.close === null && h.open !== null) {
            // If close is null but we have other data, it might be an active trading day.
            // Use current price if available, otherwise high/low average.
            return {
              ...h,
              close: quote.regularMarketPrice || (h.high + h.low) / 2
            };
          }
          return h;
        }).filter((h: any) => h.close !== null && h.date !== null);
        
        if (historical.length === 0) {
          console.log(`[FutuService] Yahoo history totally empty for ${symbol}`);
        }
      } catch (hist_err) {
        console.warn(`[FutuService] Yahoo historical fetch partial fail for ${symbol}, returning empty history.`);
      }

      const sorted_history = historical
        .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
        .map((d) => {
          const dateObj = new Date(d.date);
          let dateStr: string;
          if (normalizedRange === '1D') {
            dateStr = dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
          } else {
            dateStr = dateObj.toISOString().split("T")[0];
          }
          return {
            date: dateStr,
            price: parseFloat((d.close ?? quote.regularMarketPrice ?? 0).toFixed(2)),
            open: parseFloat((d.open ?? quote.regularMarketPrice ?? 0).toFixed(2)),
            high: parseFloat((d.high ?? quote.regularMarketPrice ?? 0).toFixed(2)),
            low: parseFloat((d.low ?? quote.regularMarketPrice ?? 0).toFixed(2)),
            volume: d.volume ?? 0,
          };
        });

      const format_market_cap = (val?: number | null): string => {
        if (!val) return "N/A";
        if (val >= 1e12) return `${(val / 1e12).toFixed(2)}T`;
        if (val >= 1e9) return `${(val / 1e9).toFixed(2)}B`;
        return `${(val / 1e6).toFixed(2)}M`;
      };

      return {
        symbol: (quote.symbol || symbol).toUpperCase(),
        name: quote.longName || quote.shortName || symbol,
        price: parseFloat((quote.regularMarketPrice ?? 0).toFixed(2)),
        change: parseFloat((quote.regularMarketChange ?? 0).toFixed(2)),
        changePercent: parseFloat((quote.regularMarketChangePercent ?? 0).toFixed(2)),
        high: parseFloat((quote.regularMarketDayHigh ?? 0).toFixed(2)),
        low: parseFloat((quote.regularMarketDayLow ?? 0).toFixed(2)),
        volume: quote.regularMarketVolume ?? 0,
        marketCap: format_market_cap(quote.marketCap),
        market: quote.fullExchangeName || quote.exchange || "US Market",
        peRatio: parseFloat((quote.trailingPE ?? 0).toFixed(2)),
        history: sorted_history,
      };
    } catch (error) {
      console.error(`[FutuService] Yahoo Finance error for ${symbol}:`, error);
      throw new Error(`Unable to fetch data for ${symbol}`);
    }
  }

  async get_trending_stocks(): Promise<string[]> {
    try {
      const result: any = await yahooFinance.trendingSymbols("US", { count: 10 });
      const symbols: string[] = (result.quotes || [])
        .map((q: any) => q.symbol as string)
        .filter((s: string) => !s.includes(".") && s.length <= 5)
        .slice(0, 7);
      return symbols.length >= 3 ? symbols : ["NVDA", "AAPL", "TSLA", "MSFT", "GOOGL", "AMD", "META"];
    } catch (_error) {
      console.warn("[FutuService] Trending fetch failed, using defaults");
      return ["NVDA", "AAPL", "TSLA", "MSFT", "GOOGL", "AMD", "META"];
    }
  }

  async get_market_indices(): Promise<MarketIndex[]> {
    const index_symbols = [
      { symbol: "^GSPC", name: "S&P 500" },
      { symbol: "^IXIC", name: "Nasdaq" },
      { symbol: "^HSI", name: "Hang Seng" },
    ];

    const results = await Promise.allSettled(
      index_symbols.map((idx) => yahooFinance.quote(idx.symbol)),
    );

    return results.map((result, i) => {
      if (result.status === "fulfilled") {
        const q: any = result.value;
        return {
          name: index_symbols[i].name,
          symbol: index_symbols[i].symbol,
          value: parseFloat((q.regularMarketPrice ?? 0).toFixed(2)),
          change_percent: parseFloat((q.regularMarketChangePercent ?? 0).toFixed(2)),
        };
      }
      return { name: index_symbols[i].name, symbol: index_symbols[i].symbol, value: 0, change_percent: 0 };
    });
  }

  async get_stock_news(symbol: string): Promise<NewsItem[]> {
    try {
      return await this.yahooBreaker.execute(async () => {
        const result: any = await yahooFinance.search(symbol, { newsCount: 5, quotesCount: 0 });
        const news_items: any[] = result.news || [];

        return news_items.slice(0, 5).map((item: any) => {
          const pub_date = new Date((item.providerPublishTime ?? 0) * 1000);
          const hours_ago = Math.floor((Date.now() - pub_date.getTime()) / 3600000);
          const time_label =
            hours_ago < 1 ? "Just now" : hours_ago < 24 ? `${hours_ago}h ago` : `${Math.floor(hours_ago / 24)}d ago`;

          const lower_title = (item.title || "").toLowerCase();
          let sentiment: "Positive" | "Negative" | "Neutral" = "Neutral";
          const positive_keywords = ["beats", "exceeds", "surges", "gains", "rises", "profit", "growth", "record", "upgrade", "rally"];
          const negative_keywords = ["miss", "falls", "drops", "decline", "loss", "concern", "downgrade", "cut", "layoff", "crash", "slump"];
          if (positive_keywords.some((k) => lower_title.includes(k))) sentiment = "Positive";
          else if (negative_keywords.some((k) => lower_title.includes(k))) sentiment = "Negative";

          return {
            title: item.title || "No title",
            time: time_label,
            sentiment,
            url: item.link,
          };
        });
      });
    } catch (_error) {
      console.warn(`[FutuService] News fetch failed for ${symbol}`);
      return [];
    }
  }
}

export const futu_service = new FutuService();
