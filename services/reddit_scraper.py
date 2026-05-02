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
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            new_contents = []
            
            for sub in SUBREDDITS:
                try:
                    url = f"https://www.reddit.com/r/{sub}/.rss"
                    response = requests.get(url, headers=headers, timeout=10)
                    parsed = feedparser.parse(response.text)
                    
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
                            new_contents.append(f"r/{sub}: {title}")
                            new_count += 1
                            
                except Exception as e:
                    logger.error(f"[RedditScraper] Failed to fetch r/{sub}: {e}")
            
            db.commit()
            
            if new_count > 0:
                # Use AI to summarize the new posts
                from services.ai_service import ai_service
                summary = ai_service.summarize_content("Reddit", new_contents)
                
                timestamp = datetime.now().strftime("%H:%M")
                message = (
                    f"───────────────────────────\n"
                    f"🤖 REDDIT INTEL — {timestamp}\n"
                    f"───────────────────────────\n"
                    f"{summary}\n\n"
                    f"📈 *New Posts:* {new_count}"
                )
                notification_queue.enqueue(message, category="news")
                logger.info(f"[RedditScraper] Check complete. Sent AI summary for {new_count} posts.")
            else:
                logger.info("[RedditScraper] No new posts found.")
                
            return new_count
            
        except Exception as e:
            logger.error(f"[RedditScraper] Fatal error: {e}")
            db.rollback()
            return 0
        finally:
            db.close()

reddit_scraper = RedditScraper()
