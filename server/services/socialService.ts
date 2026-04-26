import axios from "axios";
import { query } from "../db/database";
import { ollamaService } from "./ollamaService";


interface SocialMention {
  symbol: string;
  platform: string;
  content: string;
  author: string;
  url: string;
  upvotes: number;
  comments: number;
  mentionedAt: Date;
}

interface ViralAlert {
  symbol: string;
  platform: string;
  viralityScore: number;
  sentiment: string;
  content: string;
}

export class SocialService {
  private isRunning = false;
  private readonly US_SUBREDDITS = ['wallstreetbets', 'stocks', 'investing', 'StockMarket', 'securityanalysis'];
  
  // Hourly Stat Tracking
  private hourlyStats = {
    startTime: new Date(),
    symbolsChecked: new Set<string>(),
    mentionsFound: 0,
    sources: { reddit: 0, stocktwits: 0 }
  };


  /**
   * Fetch Reddit mentions for a symbol
   */
  public async fetchRedditMentions(symbol: string): Promise<SocialMention[]> {
    const mentions: SocialMention[] = [];
    const tickerPattern = new RegExp(`\\$?${symbol}\\b`, 'i');

    const subreddits = this.US_SUBREDDITS;

    for (const subreddit of subreddits) {
      try {
        const url = `https://www.reddit.com/r/${subreddit}/search.json?q=${symbol}&restrict_sr=1&sort=new&limit=25`;
        const response = await axios.get(url, {
          timeout: 10000,
          headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
          }
        });

        const posts = response.data?.data?.children || [];
        
        for (const post of posts) {
          const data = post.data;
          const title = data.title || '';
          const selftext = data.selftext || '';
          const content = `${title} ${selftext}`;

          // Check if symbol is actually mentioned
          if (tickerPattern.test(content)) {
            mentions.push({
              symbol: symbol.toUpperCase(),
              platform: 'reddit',
              content: content.substring(0, 500),
              author: data.author,
              url: `https://reddit.com${data.permalink}`,
              upvotes: data.ups || 0,
              comments: data.num_comments || 0,
              mentionedAt: new Date(data.created_utc * 1000)
            });
          }
        }

        // Rate limit
        await this.delay(500);
      } catch (error) {
        console.log(`[SocialService] Reddit fetch failed for r/${subreddit}:`, (error as Error).message);
      }
    }

    return mentions;
  }

  /**
   * Fetch StockTwits mentions for a symbol
   */
  public async fetchStockTwitsMentions(symbol: string): Promise<SocialMention[]> {
    const mentions: SocialMention[] = [];
    try {
      const stSymbol = symbol.toUpperCase();
      const url = `https://api.stocktwits.com/api/2/streams/symbol/${stSymbol}.json`;
      
      const response = await axios.get(url, { timeout: 10000 });
      const messages = response.data?.messages || [];

      for (const msg of messages) {
        mentions.push({
          symbol: symbol.toUpperCase(),
          platform: 'stocktwits',
          content: msg.body,
          author: msg.user?.username || 'unknown',
          url: `https://stocktwits.com/message/${msg.id}`,
          upvotes: msg.likes?.total || 0, // mapping likes to upvotes
          comments: msg.reshares_count || 0, // mapping reshares to comments
          mentionedAt: new Date(msg.created_at)
        });
      }
    } catch (error) {
      console.log(`[SocialService] StockTwits fetch failed for ${symbol}:`, (error as Error).message);
    }
    return mentions;
  }


  /**
   * Analyze sentiment for social mentions
   */
  public async analyzeMentions(mentions: SocialMention[]): Promise<SocialMention[]> {
    const results: (SocialMention & { sentimentScore: number; sentimentLabel: string; viralityScore: number })[] = [];

    for (const mention of mentions) {
      // Check if already analyzed
      const existingResult = await query(
        'SELECT sentiment_score, sentiment_label, virality_score FROM social_mentions WHERE url = $1',
        [mention.url]
      );
      const existing = existingResult.rows[0];

      if (existing) {
        results.push({
          ...mention,
          sentimentScore: existing.sentiment_score,
          sentimentLabel: existing.sentiment_label,
          viralityScore: existing.virality_score
        });
        continue;
      }

      // Calculate virality score (0-100)
      const viralityScore = this.calculateVirality(mention.upvotes, mention.comments);

      // Analyze sentiment
      const sentiment = await ollamaService.analyzeSentiment(mention.content);

      // Store in DB
      await query(`
        INSERT INTO social_mentions
        (symbol, platform, content, author, url, upvotes, comments, sentiment_score, sentiment_label, virality_score, mentioned_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (url) DO NOTHING
      `, [
        mention.symbol,
        mention.platform,
        mention.content,
        mention.author,
        mention.url,
        mention.upvotes,
        mention.comments,
        sentiment.score,
        sentiment.label,
        viralityScore,
        mention.mentionedAt.toISOString()
      ]);

      results.push({
        ...mention,
        sentimentScore: sentiment.score,
        sentimentLabel: sentiment.label,
        viralityScore
      });
    }

    return results;
  }

  /**
   * Calculate virality score based on engagement
   */
  private calculateVirality(upvotes: number, comments: number): number {
    // Normalize: log scale with caps
    const upvoteScore = Math.min(100, Math.log10(Math.max(1, upvotes)) * 20);
    const commentScore = Math.min(100, Math.log10(Math.max(1, comments)) * 30);
    const combined = (upvoteScore * 0.6) + (commentScore * 0.4);
    return Math.min(100, Math.max(0, combined));
  }

  /**
   * Get social sentiment summary
   */
  public async getSentimentSummary(symbol: string): Promise<{
    avgScore: number;
    mentionCount: number;
    trending: boolean;
    topMentions: any[];
  }> {
    const twentyFourHoursAgo = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    
    const result = await query(`
      SELECT content, upvotes, comments, sentiment_score, virality_score, platform, mentioned_at
      FROM social_mentions
      WHERE symbol = $1 AND mentioned_at > $2
      ORDER BY virality_score DESC
    `, [symbol.toUpperCase(), twentyFourHoursAgo]);
    const rows = result.rows;

    if (rows.length === 0) {
      return { avgScore: 0, mentionCount: 0, trending: false, topMentions: [] };
    }

    // Weight by virality
    const weightedScore = rows.reduce((sum: number, r: any) => sum + (parseFloat(r.sentiment_score) * (parseFloat(r.virality_score) / 100)), 0) / rows.length;
    
    // Trending if high virality mentions
    const trending = rows.some((r: any) => parseFloat(r.virality_score) > 50);

    return {
      avgScore: parseFloat(weightedScore.toFixed(3)),
      mentionCount: rows.length,
      trending,
      topMentions: rows.slice(0, 3)
    };
  }

  /**
   * Detect viral alerts (high virality + extreme sentiment)
   */
  public async detectViralAlerts(symbol: string): Promise<ViralAlert | null> {
    const twentyFourHoursAgo = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    
    const result = await query(`
      SELECT content, platform, virality_score, sentiment_score, sentiment_label
      FROM social_mentions
      WHERE symbol = $1 AND mentioned_at > $2 AND virality_score > 50
      ORDER BY virality_score DESC
      LIMIT 5
    `, [symbol.toUpperCase(), twentyFourHoursAgo]);
    const rows = result.rows;

    if (rows.length === 0) return null;

    // Check for extreme sentiment on viral content
    for (const row of rows) {
      if (Math.abs(parseFloat(row.sentiment_score)) > 0.6) {
        return {
          symbol,
          platform: row.platform,
          viralityScore: parseFloat(row.virality_score),
          sentiment: row.sentiment_label,
          content: row.content.substring(0, 200)
        };
      }
    }

    return null;
  }

  /**
   * Run 30-minute social check
   */
  public async runSocialCheck(symbols: string[]): Promise<void> {
    if (this.isRunning) {
      console.log("[SocialService] Social check already running, skipping");
      return;
    }

    this.isRunning = true;
    console.log(`[SocialService] Running social check for ${symbols.length} symbols...`);

    try {
      for (const symbol of symbols) {
        try {
          this.hourlyStats.symbolsChecked.add(symbol);

          // Fetch Reddit mentions
          const rMentions = await this.fetchRedditMentions(symbol);
          if (rMentions.length > 0) {
            await this.analyzeMentions(rMentions);
            this.hourlyStats.mentionsFound += rMentions.length;
            this.hourlyStats.sources.reddit += rMentions.length;
            console.log(`[SocialService] Processed ${rMentions.length} Reddit mentions for ${symbol}`);
          }

          // Fetch StockTwits mentions (Phase 2 Expansion)
          const stMentions = await this.fetchStockTwitsMentions(symbol);
          if (stMentions.length > 0) {
            await this.analyzeMentions(stMentions);
            this.hourlyStats.mentionsFound += stMentions.length;
            this.hourlyStats.sources.stocktwits += stMentions.length;
            console.log(`[SocialService] Processed ${stMentions.length} StockTwits mentions for ${symbol}`);
          }

          // Check for viral alerts
          const viral = await this.detectViralAlerts(symbol);
          if (viral) {
            console.log(`🔥 Viral Social Alert for ${symbol}: Virality Score ${viral.viralityScore.toFixed(0)}`);
          }

          // Rate limit
          await this.delay(2000);

        } catch (error) {
          console.error(`[SocialService] Error checking ${symbol}:`, error);
        }
      }

      console.log("[SocialService] Social check complete");
    } finally {
      this.isRunning = false;
    }
  }

  /**
   * Get trending symbols (high mention volume)
   */
  public async getTrendingSymbols(limit: number = 10): Promise<{ symbol: string; mentionCount: number; avgVirality: number }[]> {
    const twentyFourHoursAgo = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    
    const result = await query(`
      SELECT symbol, COUNT(*) as count, AVG(virality_score) as avg_virality
      FROM social_mentions
      WHERE mentioned_at > $1
      GROUP BY symbol
      ORDER BY count DESC, avg_virality DESC
      LIMIT $2
    `, [twentyFourHoursAgo, limit]);
    return result.rows.map((r: any) => ({
      symbol: r.symbol,
      mentionCount: parseInt(r.count),
      avgVirality: parseFloat(r.avg_virality)
    }));
  }

  /**
   * Get service status
   */
  public async getStatus(): Promise<{ enabled: boolean; sources: string[]; lastCheck: string | null }> {
    const result = await query('SELECT MAX(mentioned_at) as last FROM social_mentions');
    const lastRow = result.rows[0];
    
    return {
      enabled: true,
      sources: ['reddit', 'stocktwits'],
      lastCheck: lastRow?.last || null
    };
  }

  /**
   * Generates the hourly scrape conclusion for Telegram
   */
  public getHourlyConclusion(): string {
    const symbols = Array.from(this.hourlyStats.symbolsChecked);
    const mentions = this.hourlyStats.mentionsFound;
    
    let message = `🕵️ *Social Scrape Digest (Hourly)*\n` +
                  `━━━━━━━━━━━━━━━━━━━━━━\n` +
                  `⏱️ *Time Window:* ${this.hourlyStats.startTime.toLocaleTimeString()} - ${new Date().toLocaleTimeString()}\n` +
                  `📈 *Symbols Scanned:* ${symbols.length}\n` +
                  `💬 *Total Mentions Found:* ${mentions}\n` +
                  `━━━━━━━━━━━━━━━━━━━━━━\n` +
                  `📱 *Source Breakdown:*\n` +
                  `• Reddit: ${this.hourlyStats.sources.reddit}\n` +
                  `• StockTwits: ${this.hourlyStats.sources.stocktwits}\n\n`;

    if (mentions > 0) {
      message += `🔥 *Most Discussed:* \n${symbols.slice(0, 3).map(s => `• \`${s}\``).join('\n')}\n`;
    } else {
      message += `😴 _Low volume period across social sources._\n`;
    }

    message += `\n_Next digest in 1 hour._`;

    // Reset stats for next hour
    this.hourlyStats = {
      startTime: new Date(),
      symbolsChecked: new Set<string>(),
      mentionsFound: 0,
      sources: { reddit: 0, stocktwits: 0 }
    };

    return message;
  }


  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

export const socialService = new SocialService();
