import axios from "axios";
import { query } from "../db/database";
import { ollamaService } from "./ollamaService";
import { macroEventAnalyzer } from "./macroEventAnalyzer";
import dotenv from "dotenv";
import { futu_service } from './futuService';

dotenv.config();

const FINNHUB_API_KEY = process.env.FINNHUB_API_KEY;

interface NewsArticle {
  symbol: string;
  headline: string;
  source: string;
  url: string;
  summary?: string;
  publishedAt: Date;
}

interface NewsWithSentiment extends NewsArticle {
  sentimentScore: number;
  sentimentLabel: string;
}

export class NewsService {
  private isRunning = false;

  /**
   * Fetch news for a symbol from multiple sources
   */
  public async fetchNewsForSymbol(symbol: string): Promise<NewsArticle[]> {
    // Primary: futu_service.get_stock_news() — respects the shared Yahoo circuit breaker
    try {
      const newsItems = await futu_service.get_stock_news(symbol);
      if (newsItems.length > 0) {
        return newsItems.map(n => ({
          symbol: symbol.toUpperCase(),
          headline: n.title,
          source: 'Yahoo Finance',
          url: n.url || '',
          summary: n.title,
          publishedAt: new Date()
        }));
      }
    } catch { /* circuit open or rate limit — fall through to Finnhub */ }

    // Secondary: Finnhub (if API key configured)
    if (FINNHUB_API_KEY) {
      try {
        const finnhubNews = await this.fetchFromFinnhub(symbol);
        return finnhubNews;
      } catch (error) {
        console.log(`[NewsService] Finnhub fetch failed for ${symbol}`);
      }
    }

    return [];
  }

  private async fetchFromFinnhub(symbol: string): Promise<NewsArticle[]> {
    const url = `https://finnhub.io/api/v1/company-news?symbol=${symbol}&from=${this.getDateString(7)}&to=${this.getDateString(0)}&token=${FINNHUB_API_KEY}`;
    
    const response = await axios.get(url, { timeout: 10000 });
    const data = response.data || [];

    return data.slice(0, 10).map((item: any) => ({
      symbol: symbol.toUpperCase(),
      headline: item.headline,
      source: item.source,
      url: item.url,
      summary: item.summary,
      publishedAt: new Date(item.datetime * 1000)
    }));
  }


  /**
   * Analyze sentiment for a batch of articles
   */
  public async analyzeSentiment(articles: NewsArticle[]): Promise<NewsWithSentiment[]> {
    const results: NewsWithSentiment[] = [];

    for (const article of articles) {
      // Check if already analyzed
      const existingResult = await query(
        'SELECT sentiment_score, sentiment_label FROM news_articles WHERE url = $1',
        [article.url]
      );
      const existing = existingResult.rows[0];

      if (existing) {
        results.push({
          ...article,
          sentimentScore: existing.sentiment_score,
          sentimentLabel: existing.sentiment_label
        });
        continue;
      }

      // Use Ollama for sentiment (free, local)
      const sentiment = await ollamaService.analyzeSentiment(
        `${article.headline}. ${article.summary || ''}`
      );

      // Store in DB
      await query(`
        INSERT INTO news_articles 
        (symbol, headline, source, url, summary, published_at, sentiment_score, sentiment_label)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (url) DO NOTHING
      `, [
        article.symbol,
        article.headline,
        article.source,
        article.url,
        article.summary || null,
        article.publishedAt.toISOString(),
        sentiment.score,
        sentiment.label
      ]);

      results.push({
        ...article,
        sentimentScore: sentiment.score,
        sentimentLabel: sentiment.label
      });
    }

    return results;
  }

  /**
   * Get aggregated sentiment for a symbol (last 24h)
   */
  public async getSentimentSummary(symbol: string): Promise<{ 
    avgScore: number; 
    articleCount: number; 
    sentimentTrend: string;
    latestHeadlines: string[];
  }> {
    const twentyFourHoursAgo = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    
    const result = await query(`
      SELECT headline, sentiment_score, sentiment_label, published_at
      FROM news_articles
      WHERE symbol = $1 AND published_at > $2
      ORDER BY published_at DESC
    `, [symbol.toUpperCase(), twentyFourHoursAgo]);
    const rows = result.rows;

    if (rows.length === 0) {
      return { avgScore: 0, articleCount: 0, sentimentTrend: "neutral", latestHeadlines: [] };
    }

    const avgScore = rows.reduce((sum: number, r: any) => sum + parseFloat(r.sentiment_score), 0) / rows.length;
    const sentimentTrend = avgScore > 0.3 ? "positive" : avgScore < -0.3 ? "negative" : "neutral";

    return {
      avgScore: parseFloat(avgScore.toFixed(3)),
      articleCount: rows.length,
      sentimentTrend,
      latestHeadlines: rows.slice(0, 5).map((r: any) => r.headline)
    };
  }

  /**
   * Check if there's a sentiment spike (news risk)
   */
  public async checkSentimentRisk(symbol: string): Promise<{
    riskDetected: boolean;
    riskLevel: string;
    reason: string;
    articles: string[];
  }> {
    const summary = await this.getSentimentSummary(symbol);
    
    // Negative sentiment spike
    if (summary.avgScore < -0.5 && summary.articleCount >= 2) {
      return {
        riskDetected: true,
        riskLevel: "HIGH",
        reason: `Negative sentiment spike (${summary.avgScore.toFixed(2)}) from ${summary.articleCount} articles`,
        articles: summary.latestHeadlines
      };
    }

    // Unusual volume of negative news
    if (summary.articleCount >= 5 && summary.avgScore < -0.2) {
      return {
        riskDetected: true,
        riskLevel: "MEDIUM",
        reason: `Elevated negative news volume (${summary.articleCount} articles)`,
        articles: summary.latestHeadlines
      };
    }

    return {
      riskDetected: false,
      riskLevel: "LOW",
      reason: "No significant sentiment risk detected",
      articles: []
    };
  }

  /**
   * Hourly news check for all watchlist symbols
   */
  public async runHourlyCheck(symbols: string[]): Promise<void> {
    if (this.isRunning) {
      console.log("[NewsService] Hourly check already running, skipping");
      return;
    }

    this.isRunning = true;
    console.log(`[NewsService] Running hourly check for ${symbols.length} symbols...`);

    try {
      for (const symbol of symbols) {
        try {
          // Fetch and analyze
          const articles = await this.fetchNewsForSymbol(symbol);
          if (articles.length > 0) {
            await this.analyzeSentiment(articles);
          }

          // Check for risk
          const risk = await this.checkSentimentRisk(symbol);
          if (risk.riskDetected) {
            console.log(`[NewsService] News Risk Detected for ${symbol}: ${risk.reason}`);
          }

          // Rate limit
          await this.delay(1000);
        } catch (error) {
          console.error(`[NewsService] Error checking ${symbol}:`, error);
        }
      }

      // Macro event reasoning pass — feeds recent headlines to Ollama
      await this.processMacroEvents();

      console.log("[NewsService] Hourly check complete");
    } finally {
      this.isRunning = false;
    }
  }

  /**
   * After the hourly fetch, pull recent headlines and send high-severity ones
   * to macroEventAnalyzer for geopolitical/economic reasoning via Ollama.
   */
  private async processMacroEvents(): Promise<void> {
    try {
      const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
      const result = await query(`
        SELECT headline, source FROM news_articles
        WHERE published_at > $1
        ORDER BY published_at DESC
        LIMIT 30
      `, [oneHourAgo]);

      for (const row of result.rows) {
        await macroEventAnalyzer.processNews(row.headline, row.source);
      }
    } catch (error) {
      console.error("[NewsService] processMacroEvents failed:", error);
    }
  }

  /**
   * Get latest news for display
   */
  public async getLatestNews(symbol: string, limit: number = 5): Promise<any[]> {
    const result = await query(`
      SELECT headline, source, sentiment_score, sentiment_label, published_at, url
      FROM news_articles
      WHERE symbol = $1
      ORDER BY published_at DESC
      LIMIT $2
    `, [symbol.toUpperCase(), limit]);
    return result.rows;
  }

  private getDateString(daysAgo: number): string {
    const date = new Date();
    date.setDate(date.getDate() - daysAgo);
    return date.toISOString().split('T')[0];
  }

  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

export const newsService = new NewsService();
