import Parser from 'rss-parser';
import { query } from '../../db/postgres';

import { logger } from '../../core/Logger';
import { sendTelegramMessage } from '../../services/telegramService';
import { v4 as uuidv4 } from 'uuid';

export interface NewsIntelBatch {
  correlation_id: string;
  timestamp: string | Date;
  headlines: string[];
  source?: string;
}

export class NewsIntelAgent {
  private parser: Parser;
  private sources = [
    { name: 'BBC Business',    url: 'http://feeds.bbci.co.uk/news/business/rss.xml' },
    { name: 'Reuters Business', url: 'https://feeds.reuters.com/reuters/businessNews' },
    { name: 'CNBC Markets',    url: 'https://www.cnbc.com/id/100003114/device/rss/rss.html' },
    { name: 'CNBC Economy',    url: 'https://www.cnbc.com/id/20910258/device/rss/rss.html' },
    { name: 'MarketWatch',     url: 'https://www.marketwatch.com/rss/topstories' },
    { name: 'Investing.com',   url: 'https://www.investing.com/rss/news.rss' },
    { name: 'Google News',     url: 'https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en' },
  ];

  constructor() {
    this.parser = new Parser();
  }

  /**
   * Scrapes all broad news sources
   */
  public async scrapeBroadNews(mode: 'standard' | 'post-market' | 'weekend' = 'standard'): Promise<NewsIntelBatch> {
    logger.info(`[NewsIntelAgent] Starting ${mode} broad news scrape...`);
    const allArticles: any[] = [];
    const headlines: string[] = [];

    for (const source of this.sources) {
      try {
        logger.info(`[NewsIntelAgent] Scraping ${source.name}...`);
        const feed = await this.parser.parseURL(source.url);
        
        // Cap at 50 articles per source
        const items = feed.items.slice(0, 50);
        for (const item of items) {
          if (!item.title || !item.link) continue;

          const article = {
            headline: item.title,
            summary: item.contentSnippet || item.content || '',
            source: source.name,
            url: item.link,
            scraped_at: new Date()
          };

          allArticles.push(article);
          headlines.push(item.title);
        }
      } catch (error) {
        logger.error(`[NewsIntelAgent] Error scraping ${source.name}:`, { error });
      }
    }

    // Final safety cap: 100 articles total per batch
    const limitedArticles = allArticles.slice(0, 100);

    // Deduplicate and store
    const uniqueArticles = await this.deduplicateAndStore(limitedArticles);
    
    logger.info(`[NewsIntelAgent] Scrape complete. Found ${uniqueArticles.length} unique articles.`);
    
    if (uniqueArticles.length > 0) {
      await this.sendRecap(uniqueArticles, mode);
    }
    
    return {
      correlation_id: uuidv4(),
      timestamp: new Date(),
      headlines: uniqueArticles.map(a => a.headline).slice(0, 100),
      source: 'Broad'
    };
  }

  /**
   * Scrapes targeted news for symbols in the user's watchlist
   */
  public async scrapeWatchlistNews(): Promise<NewsIntelBatch> {
    logger.info('[NewsIntelAgent] Starting targeted watchlist news scrape...');
    
    const watchlist = await query("SELECT symbol FROM user_watchlist");
    const symbols = watchlist.rows.map((r: any) => r.symbol);
    
    if (symbols.length === 0) {
      logger.info('[NewsIntelAgent] Watchlist is empty, skipping targeted scrape.');
      return { correlation_id: uuidv4(), timestamp: new Date(), headlines: [], source: 'Watchlist' };
    }

    const allArticles: any[] = [];
    const headlines: string[] = [];

    // Use Google News RSS per symbol — rate-limit free, same parser as broad scrape
    for (const symbol of symbols) {
      try {
        const rssUrl = `https://news.google.com/rss/search?q=${encodeURIComponent(symbol)}+stock&hl=en-US&gl=US&ceid=US:en`;
        logger.info(`[NewsIntelAgent] Fetching targeted news for ${symbol} via RSS...`);

        const feed = await this.parser.parseURL(rssUrl);
        const items = feed.items.slice(0, 5);

        for (const item of items) {
          if (!item.title || !item.link) continue;
          allArticles.push({
            headline: `[${symbol}] ${item.title}`,
            summary: item.contentSnippet || item.title,
            source: 'Google News RSS',
            url: item.link,
            scraped_at: new Date()
          });
          headlines.push(`[${symbol}] ${item.title}`);
        }
      } catch (error: any) {
        logger.error(`[NewsIntelAgent] Targeted scrape failed for ${symbol}:`, { error: error.message });
      }
    }

    // Deduplicate and store
    const uniqueArticles = await this.deduplicateAndStore(allArticles);
    logger.info(`[NewsIntelAgent] Watchlist scrape complete. Found ${uniqueArticles.length} new targeted articles.`);
    
    return {
      correlation_id: uuidv4(),
      timestamp: new Date(),
      headlines: uniqueArticles.map(a => a.headline),
      source: 'Watchlist'
    };
  }

  /**
   * Deduplicates articles by URL and stores them in news_intel table
   */
  private async deduplicateAndStore(articles: any[]): Promise<any[]> {
    const uniqueArticles: any[] = [];
    
    for (const article of articles) {
      try {
        // ON CONFLICT (url) DO NOTHING ensures deduplication
        const result = await query(`
          INSERT INTO news_intel (headline, summary, source, url, scraped_at)
          VALUES ($1, $2, $3, $4, $5)
          ON CONFLICT (url) DO NOTHING
          RETURNING *
        `, [
          article.headline,
          article.summary,
          article.source,
          article.url,
          article.scraped_at
        ]);

        if (result.rowCount > 0) {
          uniqueArticles.push(result.rows[0]);
        }
      } catch (error) {
        console.error(`[NewsIntelAgent] Error storing article:`, error);
      }
    }

    return uniqueArticles;
  }

  /**
   * Sends a summary of newly scraped articles to Telegram
   */
  private async sendRecap(articles: any[], mode: 'standard' | 'post-market' | 'weekend' = 'standard'): Promise<void> {
    try {
      const grouped: { [source: string]: string[] } = {};
      for (const a of articles) {
        if (!grouped[a.source]) grouped[a.source] = [];
        grouped[a.source].push(a.headline);
      }

      let header = '📰 *Hourly News Found:*';
      if (mode === 'post-market') header = '🌆 *Post-Market Digest (US Close)*';
      if (mode === 'weekend') header = '🗞️ *Weekend Market Brief*';

      let message = `${header}\n\n`;
      let count = 1;
      for (const source in grouped) {
        message += `${count}. [${source}]\n`;
        // Limit to 5 headlines per source to avoid Telegram message length limits
        const headlines = grouped[source].slice(0, 5);
        for (const headline of headlines) {
          message += `- ${headline}\n`;
        }
        if (grouped[source].length > 5) {
          message += `- ...and ${grouped[source].length - 5} more\n`;
        }
        message += `\n`;
        count++;
      }

      if (mode === 'post-market') {
        const day = new Date().getDay(); // 0=Sun, 1=Mon, ..., 5=Fri
        const nextOpen = (day === 5) ? 'Monday' : 'tomorrow';
        message += `\n_Market closed. Next open: ${nextOpen} 9:30am ET._`;
      }

      await sendTelegramMessage(message.trim(), 'info');
      logger.info(`[NewsIntelAgent] Sent news recap to Telegram.`);
    } catch (error) {
      logger.error(`[NewsIntelAgent] Failed to send news recap:`, error);
    }
  }
}

export const newsIntelAgent = new NewsIntelAgent();
