import Parser from 'rss-parser';
import { query } from '../../db/postgres';
import axios from 'axios';
import * as cheerio from 'cheerio';

export interface NewsIntelBatch {
  correlation_id: string;
  timestamp: string | Date;
  headlines: string[];
  source?: string;
}

export class NewsIntelAgent {
  private parser: Parser;
  private sources = [
    { name: 'Yahoo Finance', url: 'https://finance.yahoo.com/news/rss' },
    { name: 'Google News', url: 'https://news.google.com/rss/search?q=finance+when:2h' },
    { name: 'MarketWatch', url: 'https://www.marketwatch.com/rss/topstories' },
    { name: 'SCMP Business', url: 'https://www.scmp.com/rss/318210/feed' },
    { name: 'Reuters', url: 'https://www.reutersagency.com/feed/' }
  ];

  constructor() {
    this.parser = new Parser();
  }

  /**
   * Scrapes all broad news sources
   */
  public async scrapeBroadNews(): Promise<NewsIntelBatch> {
    console.log('[NewsIntelAgent] Starting broad news scrape...');
    const allArticles: any[] = [];
    const headlines: string[] = [];

    for (const source of this.sources) {
      try {
        console.log(`[NewsIntelAgent] Scraping ${source.name}...`);
        const feed = await this.parser.parseURL(source.url);
        
        for (const item of feed.items) {
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
        console.error(`[NewsIntelAgent] Error scraping ${source.name}:`, error);
      }
    }

    // Deduplicate and store
    const uniqueArticles = await this.deduplicateAndStore(allArticles);
    
    console.log(`[NewsIntelAgent] Scrape complete. Found ${uniqueArticles.length} unique articles.`);
    
    return {
      correlation_id: '', // Will be set by publish
      timestamp: new Date(),
      headlines: uniqueArticles.map(a => a.headline),
      source: 'Broad'
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
}

export const newsIntelAgent = new NewsIntelAgent();
