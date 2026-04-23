export interface EtfEntry {
  symbol: string;
  name: string;
  sector: string;
  market: 'US' | 'HK';
  description: string;
  preferred_timeframe: 'SHORT' | 'LONG';
}

export const ETF_WATCHLIST: EtfEntry[] = [
  {
    symbol: 'XLE',
    name: 'Energy Select Sector SPDR Fund',
    sector: 'Energy',
    market: 'US',
    description: 'Tracks energy stocks, highly sensitive to oil prices.',
    preferred_timeframe: 'LONG'
  },
  {
    symbol: 'VDE',
    name: 'Vanguard Energy ETF',
    sector: 'Energy',
    market: 'US',
    description: 'Broad exposure to US energy companies.',
    preferred_timeframe: 'LONG'
  },
  {
    symbol: 'SPY',
    name: 'SPDR S&P 500 ETF Trust',
    sector: 'Broad Market',
    market: 'US',
    description: 'Tracks the S&P 500 index.',
    preferred_timeframe: 'LONG'
  },
  {
    symbol: 'QQQ',
    name: 'Invesco QQQ Trust',
    sector: 'Technology',
    market: 'US',
    description: 'Tracks the Nasdaq-100 index, heavy tech focus.',
    preferred_timeframe: 'SHORT'
  },
  {
    symbol: 'VTI',
    name: 'Vanguard Total Stock Market ETF',
    sector: 'Broad Market',
    market: 'US',
    description: 'Exposure to the entire US investable equity market.',
    preferred_timeframe: 'LONG'
  }
];
