import feedparser
from datetime import datetime
from loguru import logger
from core.database import SessionLocal, SocialPost
from services.notifications import notification_queue

ACCOUNTS = ["elonmusk", "realDonaldTrump", "federalreserve", "GaryGensler", "SECGov"]

# Nitter instances are often flaky, so we rotate
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.it",
    "https://nitter.cz",
    "https://nitter.at",
    "https://nitter.privacydev.net"
]

class XScraper:
    def run(self):
        logger.info("[XScraper] Starting check...")
        new_count = 0
        db = SessionLocal()
        
        try:
            for account in ACCOUNTS:
                success = False
                for instance in NITTER_INSTANCES:
                    try:
                        url = f"{instance}/{account}/rss"
                        parsed = feedparser.parse(url)
                        
                        if parsed.get("bozo", 0) and not parsed.entries:
                            continue # Try next instance
                            
                        for entry in parsed.entries[:5]:
                            content = entry.get("title", "")
                            post_url = entry.get("link", "")
                            
                            # Normalize post_url if it's nitter link
                            canonical_url = post_url.replace(instance, "https://twitter.com")
                            
                            if not content or not post_url:
                                continue
                                
                            existing = db.query(SocialPost).filter(SocialPost.post_url == canonical_url).first()
                            if not existing:
                                post = SocialPost(
                                    platform="X",
                                    author=account,
                                    content=content,
                                    post_url=canonical_url,
                                    posted_at=datetime.now() # RSS might not have accurate pubDate or needs parsing
                                )
                                db.add(post)
                                
                                # Immediate notification for X
                                timestamp = datetime.now().strftime("%H:%M")
                                message = (
                                    f"───────────────────────────\n"
                                    f"🐦 @{account} — {timestamp}\n"
                                    f"───────────────────────────\n"
                                    f"\"{content}\""
                                )
                                notification_queue.enqueue(message)
                                new_count += 1
                        
                        success = True
                        break # Success for this account
                    except Exception as e:
                        continue # Try next instance
                
                if not success:
                    logger.warning(f"[XScraper] Failed to fetch @{account} from all instances.")
            
            db.commit()
            logger.info(f"[XScraper] Check complete. Found {new_count} new posts.")
            return new_count
            
        except Exception as e:
            logger.error(f"[XScraper] Fatal error: {e}")
            db.rollback()
            return 0
        finally:
            db.close()

x_scraper = XScraper()
