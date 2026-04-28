import { existsSync } from 'fs';
import { logger } from '../core/Logger.js';
import { config } from '../core/Config.js';
import WebSocket from 'ws';
// @ts-ignore
import mmWebsocket from 'moomoo-api';

// Auto-detect Docker
const isDocker = existsSync('/.dockerenv');
const moomooHost = isDocker ? 'host.docker.internal' : config.MOOMOO_HOST;
const WS_PORT = config.MOOMOO_WS_PORT;

export interface Position {
  symbol: string;
  qty: number;
  avgPrice: number;
  currentPrice: number;
  pnl: string;
}

export interface Balance {
  availableCash: number;
  totalAssets: number;
  currency: string;
}

class MoomooService {
  private client: any = null;
  private accId: string | null = null;
  private trdEnv: number = 1; // Default to Real
  private trdMarket: number = 1; // Default to HK
  public isConnected: boolean = false;

  constructor() {
    this.getBalance = this.getBalance.bind(this);
    this.getPositions = this.getPositions.bind(this);
  }

  async connect(): Promise<boolean> {
    return new Promise((resolve) => {
      logger.info(`[MoomooService] Connecting via moomoo-api (WebSocket) to ${moomooHost}:${WS_PORT}...`);

      // Mock WebSocket globally for Node.js using 'ws' package
      (global as any).WebSocket = WebSocket;

      // Handle ESM default export
      // @ts-ignore
      const MM = mmWebsocket.default || mmWebsocket;
      this.client = new MM();
      
      this.client.onlogin = (ret: boolean, msg: any) => {
        if (ret) {
          logger.info('[MoomooService] Successfully connected to moomooOpenD');
          this.isConnected = true;
          this.fetchAccId().then(() => resolve(true));
        } else {
          logger.error(`[MoomooService] Connection failed: ${JSON.stringify(msg)}`);
          this.isConnected = false;
          resolve(false);
        }
      };

      this.client.onerror = (err: any) => {
        logger.error(`[MoomooService] WebSocket error: ${err.message || err}`);
      };

      try {
        this.client.start(moomooHost, WS_PORT, false, config.MOOMOO_WEBSOCKET_KEY || "");
      } catch (err: any) {
        logger.error(`[MoomooService] Start failed: ${err.message}`);
        resolve(false);
      }
    });
  }

  private async fetchAccId(retryCount = 0) {
    try {
      logger.info(`[MoomooService] Fetching account list (Attempt ${retryCount + 1})...`);
      const res = await this.client.GetAccList({
        c2s: { userID: 0 }
      });
      logger.info(`[MoomooService] GetAccList response: ${JSON.stringify(res)}`);
      if (res && res.s2c && res.s2c.accList && res.s2c.accList.length > 0) {
        // Look for a real account first (trdEnv: 1)
        const realAcc = res.s2c.accList.find((a: any) => a.trdEnv === 1);
        const simAcc = res.s2c.accList.find((a: any) => a.trdEnv === 0);
        
        if (realAcc) {
          this.accId = realAcc.accID;
          this.trdEnv = 1;
          this.trdMarket = realAcc.trdMarketAuthList?.[0] || 1;
          logger.info(`[MoomooService] Real account initialized: ${this.accId} (Market: ${this.trdMarket})`);
        } else if (simAcc) {
          this.accId = simAcc.accID;
          this.trdEnv = 0;
          this.trdMarket = simAcc.trdMarketAuthList?.[0] || 1;
          logger.info(`[MoomooService] No real accounts found. Using SIM account: ${this.accId} (Market: ${this.trdMarket})`);
        } else {
          const firstAcc = res.s2c.accList[0];
          this.accId = firstAcc.accID;
          this.trdEnv = firstAcc.trdEnv;
          this.trdMarket = firstAcc.trdMarketAuthList?.[0] || 1;
          logger.info(`[MoomooService] Using first available account: ${this.accId} (Env: ${this.trdEnv}, Market: ${this.trdMarket})`);
        }
      } else {
        logger.warn('[MoomooService] No accounts found in GetAccList');
        if (retryCount < 3) {
          logger.info('[MoomooService] Retrying fetchAccId in 5s...');
          setTimeout(() => this.fetchAccId(retryCount + 1), 5000);
        }
      }
    } catch (err: any) {
      logger.error(`[MoomooService] fetchAccId error: ${err.message || JSON.stringify(err)}`);
      if (retryCount < 3) {
        logger.info('[MoomooService] Retrying fetchAccId in 5s...');
        setTimeout(() => this.fetchAccId(retryCount + 1), 5000);
      }
    }
  }

  async getBalance(): Promise<Balance | null> {
    if (!this.client || !this.accId) {
      logger.warn(`[MoomooService] getBalance skipped: client=${!!this.client}, accId=${this.accId}`);
      return null;
    }
    try {
      logger.info(`[MoomooService] getBalance for accId=${this.accId}, trdEnv=${this.trdEnv}, trdMarket=${this.trdMarket}`);
      const res = await this.client.GetFunds({
        c2s: {
          header: {
            accID: this.accId,
            trdEnv: this.trdEnv,
            trdMarket: this.trdMarket
          }
        }
      });
      if (!res || !res.s2c) throw new Error('Empty response from GetFunds');
      return {
        availableCash: res.s2c.cash || 0,
        totalAssets: res.s2c.totalAssets || 0,
        currency: 'MYR'
      };
    } catch (err: any) {
      logger.error(`[MoomooService] getBalance failed: ${err.message || JSON.stringify(err)}`);
      return null;
    }
  }

  async getPositions(): Promise<Position[]> {
    if (!this.client || !this.accId) {
      logger.warn(`[MoomooService] getPositions skipped: client=${!!this.client}, accId=${this.accId}`);
      return [];
    }
    try {
      logger.info(`[MoomooService] getPositions for accId=${this.accId}, trdEnv=${this.trdEnv}, trdMarket=${this.trdMarket}`);
      const res = await this.client.GetPositionList({
        c2s: {
          header: {
            accID: this.accId,
            trdEnv: this.trdEnv,
            trdMarket: this.trdMarket
          }
        }
      });
      if (!res || !res.s2c) return [];
      return (res.s2c.positionList || []).map((p: any) => ({
        symbol: p.code,
        qty: p.qty,
        avgPrice: p.costPrice,
        currentPrice: p.canSellQty > 0 ? p.costPrice : 0,
        pnl: `${((p.plVal / (p.costPrice * p.qty)) * 100).toFixed(2)}%`
      }));
    } catch (err: any) {
      logger.error(`[MoomooService] getPositions failed: ${err.message || JSON.stringify(err)}`);
      return [];
    }
  }
}

export const moomooService = new MoomooService();
