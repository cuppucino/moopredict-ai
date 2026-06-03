import feedparser
from datetime import datetime
from loguru import logger
from core.database import SessionLocal, SocialPost
from services.notifications import notification_queue

ACCOUNTS = [
    # S-tier — macro / market-moving on their own
    "realDonaldTrump",
    "jeromehpowell",
    "SecScottBessent",

    # A-tier — ticker-specific market movers
    "elonmusk",
    "JensenHuang",
    "sundarpichai",
    "sama",            # Sam Altman
    "tim_cook",
    "lisasu",
    "zuck",

    # B-tier — notable but rarely solo-movers
    "JDVance",
    "BillAckman",
    "michaeljburry",
    "CathieDWood",
    "davidtepper",
    "elerianm",
    "jimcramer",
    "saylor",
    "federalreserve",
    "SECGov",
    "VitalikButerin"
]

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
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            new_contents = []
            
            for account in ACCOUNTS:
                success = False
                for instance in NITTER_INSTANCES:
                    try:
                        url = f"{instance}/{account}/rss"
                        # Reduced timeout from 10s to 5s to prevent long hangs
                        response = requests.get(url, headers=headers, timeout=5)
                        parsed = feedparser.parse(response.text)
                        
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
                                    posted_at=datetime.now()
                                )
                                db.add(post)
                                new_contents.append(f"@{account}: {content}")
                                new_count += 1
                        
                        success = True
                        break # Success for this account
                    except Exception as e:
                        continue # Try next instance
                
                if not success:
                    logger.warning(f"[XScraper] Failed to fetch @{account} from all instances.")
            
            db.commit()
            
            if new_count > 0:
                # Use AI to summarize the new posts
                from services.ai_service import ai_service
                summary = ai_service.summarize_content("X (Twitter)", new_contents)
                
                timestamp = datetime.now().strftime("%H:%M")
                message = (
                    f"───────────────────────────\n"
                    f"🐦 X INTEL — {timestamp}\n"
                    f"───────────────────────────\n"
                    f"{summary}\n\n"
                    f"📈 *New Posts:* {new_count}"
                )
                notification_queue.enqueue(message, category="news")
                logger.info(f"[XScraper] Check complete. Sent AI summary for {new_count} posts.")
            else:
                logger.info("[XScraper] No new posts found.")
                
            return new_count
            
        except Exception as e:
            logger.error(f"[XScraper] Fatal error: {e}")
            db.rollback()
            return 0
        finally:
            db.close()

x_scraper = XScraper()
