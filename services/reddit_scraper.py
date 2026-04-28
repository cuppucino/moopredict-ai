import feedparser
from datetime import datetime
from loguru import logger
from core.database import SessionLocal, SocialPost
from services.notifications import notification_queue

SUBREDDITS = ["wallstreetbets", "stocks", "investing", "options"]

class RedditScraper:
    def run(self):
        logger.info("[RedditScraper] Starting check...")
        new_count = 0
        db = SessionLocal()
        
        try:
            for sub in SUBREDDITS:
                try:
                    url = f"https://www.reddit.com/r/{sub}/.rss"
                    # User agent is important for Reddit RSS
                    parsed = feedparser.parse(url, agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
                    
                    logger.debug(f"[RedditScraper] Subreddit r/{sub} returned {len(parsed.entries)} entries.")
                    
                    for entry in parsed.entries[:10]:
                        title = entry.get("title", "")
                        post_url = entry.get("link", "")
                        author = entry.get("author", "unknown")
                        
                        if not title or not post_url:
                            continue
                            
                        existing = db.query(SocialPost).filter(SocialPost.post_url == post_url).first()
                        if not existing:
                            post = SocialPost(
                                platform="Reddit",
                                author=f"r/{sub} | {author}",
                                content=title,
                                post_url=post_url,
                                posted_at=datetime.now()
                            )
                            db.add(post)
                            
                            # For Reddit, we might want to batch, but let's notify for now
                            # (Reddit can be noisy, so maybe only notify for high-sentiment/keywords later)
                            timestamp = datetime.now().strftime("%H:%M")
                            message = (
                                f"───────────────────────────\n"
                                f"🤖 r/{sub} — {timestamp}\n"
                                f"───────────────────────────\n"
                                f"\"{title}\""
                            )
                            notification_queue.enqueue(message)
                            new_count += 1
                            
                except Exception as e:
                    logger.error(f"[RedditScraper] Failed to fetch r/{sub}: {e}")
            
            db.commit()
            logger.info(f"[RedditScraper] Check complete. Found {new_count} new posts.")
            return new_count
            
        except Exception as e:
            logger.error(f"[RedditScraper] Fatal error: {e}")
            db.rollback()
            return 0
        finally:
            db.close()

reddit_scraper = RedditScraper()
