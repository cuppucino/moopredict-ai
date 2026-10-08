import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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


def publication_time(entry):
    """Only an explicit publication date; updated/scraped times are not substitutes."""
    raw = entry.get("published")
    if not raw:
        return None
    try:
        try:
            stamp = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None

class NewsScraper:
    def run(self):
        logger.info("[NewsScraper] Starting scrape...")
        new_headlines = []
        db = SessionLocal()
        
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            for feed in FEEDS:
                try:
                    response = requests.get(feed["url"], headers=headers, timeout=10)
                    response.raise_for_status()
                    parsed = feedparser.parse(response.text)
                    logger.debug(f"[NewsScraper] Feed {feed['name']} returned {len(parsed.entries)} entries.")
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
                                url=url,
                                published_at=publication_time(entry),
                            )
                            db.add(news_item)
                            new_headlines.append(f"• [{feed['name']}] {headline}")
                except Exception as e:
                    logger.error(f"[NewsScraper] Failed to fetch {feed['name']}: {e}")
            
            db.commit()
            
            if new_headlines:
                # Use AI to summarize the news
                from services.ai_service import ai_service
                summary = ai_service.summarize_content("News", new_headlines)
                
                # Cross-reference with confirmed patterns
                from services._legacy.pattern_service import pattern_service
                patterns = pattern_service.get_all(limit=10)
                matched_rules = []
                for p in patterns:
                    if p["confirmed"] and (p["category"].lower() in summary.lower() or any(p["category"].lower() in h.lower() for h in new_headlines)):
                        matched_rules.append(f"💡 *Rule Match:* {p['name']} - {p['observation']}")
                
                rules_text = "\n\n" + "\n".join(matched_rules) if matched_rules else ""
                
                timestamp = datetime.now().strftime("%H:%M")
                message = (
                    f"───────────────────────────\n"
                    f"📰 NEWS INTEL — {timestamp}\n"
                    f"───────────────────────────\n"
                    f"{summary}"
                    f"{rules_text}\n\n"
                    f"📈 *New Articles:* {len(new_headlines)}"
                )
                notification_queue.enqueue(message, category="news")
                logger.info(f"[NewsScraper] Scrape complete. Sent AI summary and rules for {len(new_headlines)} headlines.")
            else:
                logger.info("[NewsScraper] No new articles found.")
                
            return len(new_headlines)
            
        except Exception as e:
            logger.error(f"[NewsScraper] Fatal error: {e}")
            db.rollback()
            return 0
        finally:
            db.close()

    def get_recent_for_ticker(self, ticker: str, hours: int = 24) -> list:
        """
        Return news articles mentioning the ticker or company name in the last `hours`.
        Tighter matcher: require ticker or company name to appear in the headline (not summary),
        AND require it to be in the first half of the headline OR be the only ticker mentioned.
        """
        db = SessionLocal()
        try:
            import re
            from datetime import timedelta
            time_threshold = datetime.utcnow() - timedelta(hours=hours)
            articles = db.query(NewsIntel).filter(
                NewsIntel.scraped_at >= time_threshold
            ).all()
            
            from services.political_monitor import matches_ticker, _get_sentiment, TICKER_ALIASES
            
            def strip_publisher_suffix(headline_str: str) -> str:
                pattern = r"\s*[-|]\s*(Google News|Reuters|CNBC|MarketWatch|BBC News|BBC|Yahoo Finance|Bloomberg|WSJ|Wall Street Journal)\s*$"
                return re.sub(pattern, "", headline_str, flags=re.IGNORECASE).strip()
            
            def find_first_match_index(headline_str: str, tk: str):
                tk = tk.upper()
                patterns = [
                    r"\$" + re.escape(tk) + r"\b",
                    r"\b" + re.escape(tk) + r"\b"
                ]
                aliases = TICKER_ALIASES.get(tk, [])
                for alias in aliases:
                    patterns.append(r"\b" + re.escape(alias) + r"\b")
                
                first_idx = None
                for pat in patterns:
                    m = re.search(pat, headline_str, re.IGNORECASE)
                    if m:
                        start_idx = m.start()
                        if first_idx is None or start_idx < first_idx:
                            first_idx = start_idx
                return first_idx

            results = []
            for art in articles:
                cleaned_headline = strip_publisher_suffix(art.headline)
                
                # 1. Require the ticker or company name to appear in the cleaned headline
                if not matches_ticker(cleaned_headline, ticker):
                    continue
                
                # 2. Require it to be in the first half of the headline OR be the only ticker mentioned.
                first_idx = find_first_match_index(cleaned_headline, ticker)
                if first_idx is None:
                    continue
                
                # Check if it is in the first half of the headline
                is_in_first_half = first_idx < (len(cleaned_headline) / 2)
                
                # Check if it is the only ticker mentioned (ignoring GOOG/GOOGL overlap)
                other_tickers_found = False
                for other_ticker in TICKER_ALIASES.keys():
                    if other_ticker != ticker.upper():
                        if {ticker.upper(), other_ticker} == {"GOOG", "GOOGL"}:
                            continue
                        if matches_ticker(cleaned_headline, other_ticker):
                            other_tickers_found = True
                            break
                            
                is_only_ticker = not other_tickers_found
                
                if is_in_first_half or is_only_ticker:
                    text_to_analyze = f"{art.headline} {art.summary or ''}"
                    sentiment = _get_sentiment(text_to_analyze)
                    results.append({
                        "source": art.source,
                        "timestamp": art.scraped_at.isoformat(),
                        "headline": art.headline,
                        "sentiment": sentiment
                    })
            return results
        except Exception as e:
            logger.error(f"[NewsScraper] Error in get_recent_for_ticker: {e}")
            return []
        finally:
            db.close()

news_scraper = NewsScraper()
