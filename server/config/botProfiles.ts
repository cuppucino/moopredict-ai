// Bot Profiles Configuration
// Each stock gets its own personality and strategy

export interface BotProfile {
  symbol: string;
  name: string;
  personality: 'conservative' | 'balanced' | 'aggressive' | 'scalper';
  strategy: string;
  description: string;
  
  // Trading parameters
  confidenceThreshold: number;      // 0.0 - 1.0 (higher = more selective)
  riskTolerance: 'low' | 'medium' | 'high';
  maxPositionSize: number;        // Max $ per trade
  
  // Technical analysis weights
  technicalWeight: number;        // 0.0 - 1.0
  newsWeight: number;             // 0.0 - 1.0
  socialWeight: number;           // 0.0 - 1.0
  sentimentWeight: number;        // 0.0 - 1.0
  
  // Entry conditions
  entryRules: {
    minRsi: number;              // Don't buy if RSI below this
    maxRsi: number;              // Don't buy if RSI above this (overbought)
    requireUptrend: boolean;     // Only buy in uptrend?
    minConfidence: number;       // Minimum AI confidence
    dipBuyThreshold?: number;     // Buy dips of X% (for aggressive)
  };
  
  // Exit conditions
  exitRules: {
    stopLossPercent: number;      // -5%, -3%, etc
    takeProfitPercent: number;    // +10%, +5%, etc
    trailingStop: boolean;        // Use trailing stop?
    timeLimit?: number;           // Auto exit after X hours
  };
  
  // Risk management
  maxDailyLoss: number;           // Pause if lose this much
  cooldownPeriod: number;         // Minutes between trades
  
  // Gamification
  level: number;
  xp: number;
  nextLevelXp: number;
  achievements: string[];
  energy: number;                 // 0-100
  mood: 'focused' | 'excited' | 'cautious' | 'tired';
  lastActivity: string;
}

export const DEFAULT_PROFILES: Record<string, BotProfile> = {
  AAPL: {
    symbol: 'AAPL',
    name: 'Alpha Trader',
    personality: 'balanced',
    strategy: 'Trend Follower',
    description: 'Steady and reliable. Follows established trends with confirmation.',
    
    confidenceThreshold: 0.7,
    riskTolerance: 'medium',
    maxPositionSize: 20,
    
    technicalWeight: 0.4,
    newsWeight: 0.2,
    socialWeight: 0.1,
    sentimentWeight: 0.3,
    
    entryRules: {
      minRsi: 30,
      maxRsi: 70,
      requireUptrend: true,
      minConfidence: 0.7
    },
    
    exitRules: {
      stopLossPercent: -5,
      takeProfitPercent: 10,
      trailingStop: true
    },
    
    maxDailyLoss: 15,
    cooldownPeriod: 30,
    
    level: 1,
    xp: 0,
    nextLevelXp: 100,
    achievements: [],
    energy: 85,
    mood: 'focused',
    lastActivity: 'Initializing...'
  },
  
  TSLA: {
    symbol: 'TSLA',
    name: 'Volatility Surfer',
    personality: 'aggressive',
    strategy: 'Dip Buyer',
    description: 'Loves chaos. Buys big dips and rides the waves. High risk, high reward.',
    
    confidenceThreshold: 0.6,
    riskTolerance: 'high',
    maxPositionSize: 25,
    
    technicalWeight: 0.3,
    newsWeight: 0.4,
    socialWeight: 0.2,
    sentimentWeight: 0.1,
    
    entryRules: {
      minRsi: 20,
      maxRsi: 80,
      requireUptrend: false,
      minConfidence: 0.6,
      dipBuyThreshold: -3  // Buy 3% dips
    },
    
    exitRules: {
      stopLossPercent: -8,
      takeProfitPercent: 15,
      trailingStop: true
    },
    
    maxDailyLoss: 25,
    cooldownPeriod: 15,
    
    level: 1,
    xp: 0,
    nextLevelXp: 100,
    achievements: [],
    energy: 92,
    mood: 'excited',
    lastActivity: 'Scanning for volatility...'
  },
  
  NVDA: {
    symbol: 'NVDA',
    name: 'Momentum Hunter',
    personality: 'aggressive',
    strategy: 'Breakout Trader',
    description: 'Waits for strong momentum signals. Fast entry, fast exit.',
    
    confidenceThreshold: 0.75,
    riskTolerance: 'high',
    maxPositionSize: 20,
    
    technicalWeight: 0.5,
    newsWeight: 0.2,
    socialWeight: 0.1,
    sentimentWeight: 0.2,
    
    entryRules: {
      minRsi: 40,
      maxRsi: 75,
      requireUptrend: true,
      minConfidence: 0.75
    },
    
    exitRules: {
      stopLossPercent: -4,
      takeProfitPercent: 8,
      trailingStop: true,
      timeLimit: 24  // Exit after 24 hours
    },
    
    maxDailyLoss: 20,
    cooldownPeriod: 20,
    
    level: 1,
    xp: 0,
    nextLevelXp: 100,
    achievements: [],
    energy: 88,
    mood: 'focused',
    lastActivity: 'Analyzing momentum...'
  },
  
  AMD: {
    symbol: 'AMD',
    name: 'Value Seeker',
    personality: 'conservative',
    strategy: 'Support Buyer',
    description: 'Patient and careful. Only buys near strong support levels.',
    
    confidenceThreshold: 0.8,
    riskTolerance: 'low',
    maxPositionSize: 15,
    
    technicalWeight: 0.5,
    newsWeight: 0.3,
    socialWeight: 0.1,
    sentimentWeight: 0.1,
    
    entryRules: {
      minRsi: 25,
      maxRsi: 65,
      requireUptrend: true,
      minConfidence: 0.8
    },
    
    exitRules: {
      stopLossPercent: -3,
      takeProfitPercent: 6,
      trailingStop: false
    },
    
    maxDailyLoss: 10,
    cooldownPeriod: 45,
    
    level: 1,
    xp: 0,
    nextLevelXp: 100,
    achievements: [],
    energy: 90,
    mood: 'cautious',
    lastActivity: 'Waiting for support...'
  },
  
  MSFT: {
    symbol: 'MSFT',
    name: 'Steady Eddie',
    personality: 'conservative',
    strategy: 'Dividend Growth',
    description: 'Slow and steady. Prioritizes safety over big gains.',
    
    confidenceThreshold: 0.75,
    riskTolerance: 'low',
    maxPositionSize: 15,
    
    technicalWeight: 0.4,
    newsWeight: 0.3,
    socialWeight: 0.1,
    sentimentWeight: 0.2,
    
    entryRules: {
      minRsi: 35,
      maxRsi: 65,
      requireUptrend: true,
      minConfidence: 0.75
    },
    
    exitRules: {
      stopLossPercent: -3,
      takeProfitPercent: 5,
      trailingStop: true
    },
    
    maxDailyLoss: 10,
    cooldownPeriod: 60,
    
    level: 1,
    xp: 0,
    nextLevelXp: 100,
    achievements: [],
    energy: 95,
    mood: 'focused',
    lastActivity: 'Monitoring stability...'
  },
  
  META: {
    symbol: 'META',
    name: 'Social Scanner',
    personality: 'balanced',
    strategy: 'Sentiment Trader',
    description: 'Reacts to news and social sentiment. Quick on trends.',
    
    confidenceThreshold: 0.65,
    riskTolerance: 'medium',
    maxPositionSize: 18,
    
    technicalWeight: 0.3,
    newsWeight: 0.3,
    socialWeight: 0.3,
    sentimentWeight: 0.1,
    
    entryRules: {
      minRsi: 30,
      maxRsi: 70,
      requireUptrend: false,
      minConfidence: 0.65
    },
    
    exitRules: {
      stopLossPercent: -5,
      takeProfitPercent: 10,
      trailingStop: true
    },
    
    maxDailyLoss: 15,
    cooldownPeriod: 25,
    
    level: 1,
    xp: 0,
    nextLevelXp: 100,
    achievements: [],
    energy: 87,
    mood: 'focused',
    lastActivity: 'Scanning social feeds...'
  }
};

// Personality descriptions for UI
export const PERSONALITY_DESCRIPTIONS = {
  conservative: {
    label: 'Conservative',
    color: 'text-blue-500',
    bgColor: 'bg-blue-500/10',
    icon: '🛡️',
    description: 'Prioritizes safety. Waits for strong confirmations.'
  },
  balanced: {
    label: 'Balanced',
    color: 'text-emerald-500',
    bgColor: 'bg-emerald-500/10',
    icon: '⚖️',
    description: 'Moderate risk/reward. Good for most conditions.'
  },
  aggressive: {
    label: 'Aggressive',
    color: 'text-amber-500',
    bgColor: 'bg-amber-500/10',
    icon: '⚡',
    description: 'High risk, high reward. Loves volatility.'
  },
  scalper: {
    label: 'Scalper',
    color: 'text-purple-500',
    bgColor: 'bg-purple-500/10',
    icon: '🎯',
    description: 'Quick in-and-out. Many small trades.'
  }
};

// XP calculation for leveling up
export function calculateXpGain(trade: any): number {
  let xp = 10; // Base XP for any trade
  
  if (trade.pnl && trade.pnl > 0) {
    xp += Math.floor(trade.pnl * 2); // Bonus for profit
    xp += 20; // Win bonus
  }
  
  if (trade.pnl && trade.pnl > 10) {
    xp += 50; // Big win bonus
  }
  
  return xp;
}

// Level calculation
export function calculateLevel(xp: number): number {
  // XP needed per level increases
  // Level 1: 0-100
  // Level 2: 100-250
  // Level 3: 250-450
  // etc.
  let level = 1;
  let xpNeeded = 100;
  let remainingXp = xp;
  
  while (remainingXp >= xpNeeded) {
    remainingXp -= xpNeeded;
    level++;
    xpNeeded = Math.floor(xpNeeded * 1.5);
  }
  
  return level;
}

// Achievement checks
export const ACHIEVEMENTS = [
  { id: 'first_trade', name: 'First Steps', description: 'Execute your first trade', icon: '🚀' },
  { id: 'profitable', name: 'In the Green', description: 'Close first profitable trade', icon: '💰' },
  { id: 'ten_trades', name: 'Getting Started', description: 'Execute 10 trades', icon: '📊' },
  { id: 'big_winner', name: 'Big Winner', description: 'Profit $10+ on single trade', icon: '🏆' },
  { id: 'risk_manager', name: 'Risk Manager', description: 'Stop loss saved you', icon: '🛡️' },
  { id: 'dip_buyer', name: 'Dip Buyer', description: 'Buy 5% dip successfully', icon: '📉' },
  { id: 'social_guru', name: 'Social Guru', description: 'Profit from social sentiment', icon: '👥' },
  { id: 'level_10', name: 'Veteran', description: 'Reach level 10', icon: '⭐' },
  { id: 'consistent', name: 'Consistency', description: '5 profitable trades in a row', icon: '📈' },
  { id: 'survivor', name: 'Survivor', description: 'Trade for 7 days', icon: '🗓️' }
];
