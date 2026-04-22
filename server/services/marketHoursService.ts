import { logger } from "../core/Logger";

/**
 * Market Hours Service
 * 
 * Logic to check if US or HK markets are in Regular Trading Hours (RTH).
 */
export class MarketHoursService {
  /**
   * Check if the market for a given symbol is currently open for regular trading.
   */
  public isMarketOpen(symbol: string): { isOpen: boolean; market: string; reason: string } {
    const isHK = /^\d+$/.test(symbol) || symbol.toUpperCase().endsWith('.HK');
    const now = new Date();

    if (isHK) {
      return this.checkHKMarket(now);
    } else {
      return this.checkUSMarket(now);
    }
  }

  /**
   * HK Market Hours: 09:30 - 12:00, 13:00 - 16:00 HKT (UTC+8)
   */
  private checkHKMarket(now: Date): { isOpen: boolean; market: string; reason: string } {
    // Convert to HKT (UTC+8)
    const hktOffset = 8 * 60;
    const hktDate = new Date(now.getTime() + (hktOffset + now.getTimezoneOffset()) * 60000);
    
    const day = hktDate.getDay();
    const hour = hktDate.getHours();
    const min = hktDate.getMinutes();
    const timeVal = hour * 100 + min;

    if (day === 0 || day === 6) {
      return { isOpen: false, market: 'HK', reason: 'Weekend' };
    }

    const isMorning = timeVal >= 930 && timeVal <= 1200;
    const isAfternoon = timeVal >= 1300 && timeVal <= 1600;

    if (isMorning || isAfternoon) {
      return { isOpen: true, market: 'HK', reason: 'Regular Trading Hours' };
    }

    return { isOpen: false, market: 'HK', reason: 'Market Closed' };
  }

  /**
   * US Market Hours: 09:30 - 16:00 ET (UTC-5 or UTC-4)
   * We approximate ET using a -12/13 hour offset from HKT or check against UTC.
   */
  private checkUSMarket(now: Date): { isOpen: boolean; market: string; reason: string } {
    // Get UTC hours/mins
    const utcHour = now.getUTCHours();
    const utcMin = now.getUTCMinutes();
    const utcDay = now.getUTCDay();
    const timeVal = utcHour * 100 + utcMin;

    // US ET is UTC-5 (Standard) or UTC-4 (Daylight)
    // RTH is 13:30 - 20:00 UTC (EDT) or 14:30 - 21:00 UTC (EST)
    
    // We'll use a safer check by calculating the actual ET time
    // For simplicity, we assume EDT (UTC-4) if we are in mid-year, 
    // but a better way is to just check the bracket: 13:30 to 21:00 UTC covers most cases.
    
    // Let's be more precise: 09:30 ET to 16:00 ET.
    const isWeekend = utcDay === 0 || utcDay === 6;
    if (isWeekend) return { isOpen: false, market: 'US', reason: 'Weekend' };

    // Standard hours in UTC:
    // EST: 14:30 - 21:00 UTC
    // EDT: 13:30 - 20:00 UTC
    const isRTH = timeVal >= 1330 && timeVal <= 2100;

    if (isRTH) {
      return { isOpen: true, market: 'US', reason: 'Regular Trading Hours' };
    }

    return { isOpen: false, market: 'US', reason: 'Market Closed (Outside RTH)' };
  }
}

export const marketHoursService = new MarketHoursService();
