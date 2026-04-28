import feedparser
from datetime import datetime
from loguru import logger
from core.database import SessionLocal, NewsIntel
from services.notifications import notification_queue

FEEDS = [
    {"name": "BBC", "url": "http://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "Reuters", "url": "https://www.reuters.com/arc/outboundfeeds/news-handler/?outputType=xml"},
    {"name": "CNBC", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch", "url": "https://www.marketwatch.com/rss/topstories"},
    {"name": "Google News", "url": "https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en"},
]

class NewsScraper:
    def run(self):
        logger.info("[NewsScraper] Starting scrape...")
        new_headlines = []
        db = SessionLocal()
        
        try:
            for feed in FEEDS:
                try:
                    parsed = feedparser.parse(feed["url"])
                    # Limit to top 10 items per feed
                    for entry in parsed.entries[:10]:
                        headline = entry.get("title", "")
                        url = entry.get("link", "")
                        summary = entry.get("summary", "") or entry.get("description", "")
                        
                        if not headline or not url:
                            continue
                            
                        # Check for duplicates
                        existing = db.query(NewsIntel).filter(NewsIntel.url == url).first()
                        if not existing:
                            news_item = NewsIntel(
                                headline=headline,
                                summary=summary,
                                source=feed["name"],
                                url=url
                            )
                            db.add(news_item)
                            new_headlines.append(f"• [{feed['name']}] {headline}")
                except Exception as e:
                    logger.error(f"[NewsScraper] Failed to fetch {feed['name']}: {e}")
            
            db.commit()
            
            if new_headlines:
                timestamp = datetime.now().strftime("%H:%M")
                message = (
                    f"───────────────────────────\n"
                    f"📰 NEWS BATCH — {timestamp}\n"
                    f"───────────────────────────\n"
                    + "\n".join(new_headlines[:20])
                )
                notification_queue.enqueue(message)
                logger.info(f"[NewsScraper] Scrape complete. Found {len(new_headlines)} new headlines.")
            else:
                logger.info("[NewsScraper] No new articles found.")
                
            return len(new_headlines)
            
        except Exception as e:
            logger.error(f"[NewsScraper] Fatal error: {e}")
            db.rollback()
            return 0
        finally:
            db.close()

news_scraper = NewsScraper()
