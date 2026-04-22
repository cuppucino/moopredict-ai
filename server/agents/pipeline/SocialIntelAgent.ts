import Parser from 'rss-parser';
import { query } from '../../db/postgres';
import axios from 'axios';

export class SocialIntelAgent {
  private parser: Parser;
  private redditSubs = ['wallstreetbets', 'stocks', 'HongKong', 'investing'];
  
  constructor() {
    this.parser = new Parser({
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
      }
    });
  }

  /**
   * Scrapes broad social intel
   */
  public async scrapeSocialIntel(): Promise<any> {
    console.log('[SocialIntelAgent] Starting social intel scrape...');
    const allItems: any[] = [];

    // 1. Reddit RSS
    for (const sub of this.redditSubs) {
      try {
        console.log(`[SocialIntelAgent] Scraping r/${sub}...`);
        const url = `https://www.reddit.com/r/${sub}/.rss`;
        const feed = await this.parser.parseURL(url);
        
        for (const item of feed.items) {
          allItems.push({
            platform: 'Reddit',
            content: `${item.title} ${item.contentSnippet || ''}`,
            url: item.link,
            scraped_at: new Date()
          });
        }
      } catch (error) {
        console.error(`[SocialIntelAgent] Error scraping r/${sub}:`, error);
      }
    }

    // 2. StockTwits Trending Stream
    try {
      console.log('[SocialIntelAgent] Scraping StockTwits trending...');
      const response = await axios.get('https://api.stocktwits.com/api/2/streams/trending.json', {
        headers: { 'User-Agent': 'MooPredict/1.0' }
      });
      
      if (response.data && response.data.messages) {
        for (const msg of response.data.messages) {
          allItems.push({
            platform: 'StockTwits',
            content: msg.body,
            url: `https://stocktwits.com/message/${msg.id}`,
            scraped_at: new Date()
          });
        }
      }
    } catch (error) {
      console.error('[SocialIntelAgent] Error scraping StockTwits:', error);
    }

    // Store in DB
    const storedCount = await this.storeIntel(allItems);
    console.log(`[SocialIntelAgent] Social scrape complete. Stored ${storedCount} items.`);
    
    return allItems;
  }

  private async storeIntel(items: any[]): Promise<number> {
    let count = 0;
    for (const item of items) {
      try {
        const result = await query(`
          INSERT INTO social_intel (platform, content, scraped_at)
          VALUES ($1, $2, $3)
          ON CONFLICT DO NOTHING
          RETURNING id
        `, [item.platform, item.content, item.scraped_at]);
        
        if (result.rowCount > 0) count++;
      } catch (error) {
        // Silently skip duplicates or errors
      }
    }
    return count;
  }
}

export const socialIntelAgent = new SocialIntelAgent();
