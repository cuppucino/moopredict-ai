import Parser from 'rss-parser';
import { query } from '../../db/postgres';
import axios from 'axios';
import * as cheerio from 'cheerio';

import { logger } from '../../core/Logger';
import { v4 as uuidv4 } from 'uuid';
import { createRequire } from "module";
const _require = createRequire(import.meta.url);
const yahooFinance = _require("yahoo-finance2").default;
export interface NewsIntelBatch {
  correlation_id: string;
  timestamp: string | Date;
  headlines: string[];
  source?: string;
}

export class NewsIntelAgent {
  private parser: Parser;
  private sources = [
    { name: 'Yahoo Finance RSS', url: 'https://finance.yahoo.com/news/rss' },
    { name: 'Google News', url: 'https://news.google.com/rss/search?q=finance+when:2h' },
    { name: 'MarketWatch', url: 'https://www.marketwatch.com/rss/topstories' },
    { name: 'SCMP Business', url: 'https://www.scmp.com/rss/318210/feed' }
  ];

  constructor() {
    this.parser = new Parser();
  }

  /**
   * Scrapes all broad news sources
   */
  public async scrapeBroadNews(): Promise<NewsIntelBatch> {
    logger.info('[NewsIntelAgent] Starting broad news scrape...');
    const allArticles: any[] = [];
    const headlines: string[] = [];

    for (const source of this.sources) {
      try {
        logger.info(`[NewsIntelAgent] Scraping ${source.name}...`);
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
        logger.error(`[NewsIntelAgent] Error scraping ${source.name}:`, { error });
      }
    }

    // Modern Fallback: Yahoo Finance Search for broad trends
    try {
      logger.info(`[NewsIntelAgent] Scraping Yahoo Finance Search for broad trends...`);
      const searchTerms = ['stock market news', 'financial market trends', 'breaking economy news'];
      for (const term of searchTerms) {
        const result: any = await yahooFinance.search(term, { newsCount: 5, quotesCount: 0 });
        const newsItems = result.news || [];
        
        for (const item of newsItems) {
          allArticles.push({
            headline: item.title,
            summary: item.title,
            source: 'Yahoo Search',
            url: item.link,
            scraped_at: new Date()
          });
          headlines.push(item.title);
        }
      }
    } catch (error) {
      logger.error(`[NewsIntelAgent] Yahoo Search scrape failed:`, { error });
    }

    // Deduplicate and store
    const uniqueArticles = await this.deduplicateAndStore(allArticles);
    
    logger.info(`[NewsIntelAgent] Scrape complete. Found ${uniqueArticles.length} unique articles.`);
    
    return {
      correlation_id: uuidv4(),
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
