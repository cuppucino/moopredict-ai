"""
Political Figure Twitter/X Monitor for MooPredict.

Monitors high-impact political accounts (Trump, Musk, Fed officials)
for market-moving tweets. Classifies urgency and routes alerts in
real-time to the trading pipeline.
"""

import re
import hashlib
from datetime import datetime, timedelta
from loguru import logger

from core.database import SessionLocal, SocialPost
from services.notifications import notification_queue
from services.sentiment_engine import sentiment_engine

# VIP Account Registry matching walkthrough specification
VIP_ACCOUNTS = {
    "realDonaldTrump": {
        "display": "Donald Trump",
        "tier": "S",
        "impact": "Tariffs, macro, policy",
        "emoji": "🇺🇸"
    },
    "jeromehpowell": {
        "display": "Fed Chair Powell",
        "tier": "S",
        "impact": "Interest rates, monetary policy",
        "emoji": "🏦"
    },
    "SecScottBessent": {
        "display": "Treasury Sec. Bessent",
        "tier": "S",
        "impact": "Fiscal policy, debt, trade",
        "emoji": "💵"
    },
    "elonmusk": {
        "display": "Elon Musk",
        "tier": "A",
        "impact": "TSLA, crypto, tech",
        "emoji": "🚀"
    },
    "JensenHuang": {
        "display": "Jensen Huang",
        "tier": "A",
        "impact": "NVDA, AI, semiconductors",
        "emoji": "🤖"
    },
    "sundarpichai": {
        "display": "Sundar Pichai",
        "tier": "A",
        "impact": "GOOGL, AI, search",
        "emoji": "🔍"
    },
    "sama": {
        "display": "Sam Altman",
        "tier": "A",
        "impact": "AI, OpenAI",
        "emoji": "🧠"
    },
    "tim_cook": {
        "display": "Tim Cook",
        "tier": "A",
        "impact": "AAPL, tech, supply chain",
        "emoji": "🍎"
    },
    "lisasu": {
        "display": "Lisa Su",
        "tier": "A",
        "impact": "AMD, AI, semiconductors",
        "emoji": "💻"
    },
    "zuck": {
        "display": "Mark Zuckerberg",
        "tier": "A",
        "impact": "META, social media, VR",
        "emoji": "👓"
    },
    "JDVance": {
        "display": "VP JD Vance",
        "tier": "B",
        "impact": "Administration policy, tariffs",
        "emoji": "🦅"
    },
    "BillAckman": {
        "display": "Bill Ackman",
        "tier": "B",
        "impact": "Pershing Square, activist investor",
        "emoji": "📊"
    },
    "michaeljburry": {
        "display": "Michael Burry",
        "tier": "B",
        "impact": "Scion Asset Management, macro",
        "emoji": "📉"
    },
    "CathieDWood": {
        "display": "Cathie Wood",
        "tier": "B",
        "impact": "ARK Invest, innovation",
        "emoji": "📈"
    },
    "davidtepper": {
        "display": "David Tepper",
        "tier": "B",
        "impact": "Appaloosa, equities, macro",
        "emoji": "🎯"
    },
    "elerianm": {
        "display": "Mohamed El-Erian",
        "tier": "B",
        "impact": "Allianz, economic commentary",
        "emoji": "📝"
    },
    "jimcramer": {
        "display": "Jim Cramer",
        "tier": "B",
        "impact": "CNBC Mad Money, retail sentiment",
        "emoji": "📺"
    },
    "saylor": {
        "display": "Michael Saylor",
        "tier": "B",
        "impact": "BTC, MicroStrategy",
        "emoji": "🪙"
    },
    "federalreserve": {
        "display": "Federal Reserve",
        "tier": "B",
        "impact": "Official statements, fed minutes",
        "emoji": "🏛️"
    },
    "SECGov": {
        "display": "SEC",
        "tier": "B",
        "impact": "Regulatory filings, crypto notices",
        "emoji": "⚖️"
    },
    "VitalikButerin": {
        "display": "Vitalik Buterin",
        "tier": "B",
        "impact": "ETH, crypto, web3",
        "emoji": "⛓️"
    }
}

VIP_TIERS = {
    "S": {
        "realDonaldTrump": {"display": "President Trump"},
        "jeromehpowell":   {"display": "Fed Chair Powell"},
        "SecScottBessent": {"display": "Treasury Sec. Bessent"},
    },
    "A": {
        "elonmusk":     {"display": "Elon Musk"},
        "JensenHuang":  {"display": "Jensen Huang (NVDA)"},
        "sundarpichai": {"display": "Sundar Pichai (GOOGL)"},
        "sama":         {"display": "Sam Altman (OpenAI)"},
        "tim_cook":     {"display": "Tim Cook (AAPL)"},
        "lisasu":       {"display": "Lisa Su (AMD)"},
        "zuck":         {"display": "Mark Zuckerberg (META)"},
    },
    "B": {
        "JDVance":         {"display": "VP JD Vance"},
        "BillAckman":      {"display": "Bill Ackman"},
        "michaeljburry":   {"display": "Michael Burry"},
        "CathieDWood":     {"display": "Cathie Wood (ARK)"},
        "davidtepper":     {"display": "David Tepper"},
        "elerianm":        {"display": "Mohamed El-Erian"},
        "jimcramer":       {"display": "Jim Cramer"},
        "saylor":          {"display": "Michael Saylor"},
        "federalreserve":  {"display": "Federal Reserve"},
        "SECGov":          {"display": "SEC"},
        "VitalikButerin":  {"display": "Vitalik Buterin"},
    },
}

def get_tier(handle: str) -> str:
    """Return 'S', 'A', 'B', or 'UNKNOWN' for a given handle."""
    try:
        for tier, members in VIP_TIERS.items():
            if handle in members:
                return tier
        return "UNKNOWN"
    except Exception as e:
        logger.error(f"[PoliticalMonitor] Error in get_tier for handle {handle}: {e}")
        return "UNKNOWN"

TICKER_TO_KEYWORDS = {
    "NVDA": [r"\bnvda\b", r"\bnvidia\b", r"\bjensen\b"],
    "AAPL": [r"\baapl\b", r"\bapple\b", r"\biphone\b"],
    "DELL": [r"\bdell\b"],
    "TSLA": [r"\btsla\b", r"\btesla\b", r"\belon\b"],
    "GOOGL": [r"\bgoogl\b", r"\bgoogle\b", r"\bsundar\b"],
    "META": [r"\bmeta\b", r"\bzuck\b", r"\bfacebook\b"],
    "AMD": [r"\bamd\b", r"\blisa su\b"],
    "MSFT": [r"\bmsft\b", r"\bmicrosoft\b"],
    "BTC": [r"\bbtc\b", r"\bbitcoin\b"],
    "ETH": [r"\beth\b", r"\bethereum\b", r"\bvitalik\b"]
}

def matches_ticker(text: str, ticker: str) -> bool:
    """Check if the text mentions a ticker symbol or its associated company names."""
    try:
        # Check direct $TICKER mention (e.g. $AAPL)
        if re.search(r"\$" + re.escape(ticker), text, re.IGNORECASE):
            return True
        
        # Check keywords if defined
        keywords = TICKER_TO_KEYWORDS.get(ticker.upper(), [r"\b" + re.escape(ticker) + r"\b"])
        for kw in keywords:
            if re.search(kw, text, re.IGNORECASE):
                return True
        return False
    except Exception as e:
        logger.error(f"[PoliticalMonitor] Error matching ticker {ticker}: {e}")
        return False

def _get_sentiment(text: str) -> str:
    """Simple keyword-based sentiment classification."""
    try:
        text_lower = text.lower()
        bullish_words = ["buy", "bullish", "up", "great", "long", "call"]
        bearish_words = ["sell", "bearish", "crash", "dump", "short", "put"]
        
        bull_count = sum(1 for w in bullish_words if w in text_lower)
        bear_count = sum(1 for w in bearish_words if w in text_lower)
        
        if bull_count > bear_count:
            return "bullish"
        elif bear_count > bull_count:
            return "bearish"
        return "neutral"
    except Exception as e:
        logger.error(f"[PoliticalMonitor] Error in _get_sentiment: {e}")
        return "neutral"

KEYWORD_TIERS = {
    "P0": [
        r"tariff",
        r"rate cut", r"rate hike",
        r"executive order",
        r"go buy",
        r"debt ceiling",
        r"trade deal", r"trade ban",
        r"sanction"
    ],
    "P1": [
        r"china",
        r"bitcoin",
        r"tesla",
        r"apple",
        r"antitrust",
        r"stimulus",
        r"bailout"
    ],
    "P2": [
        r"policy",
        r"market",
        r"tax",
        r"inflation",
        r"gdp"
    ]
}

_COMPILED_KEYWORDS = {
    tier: re.compile("|".join(patterns), re.IGNORECASE)
    for tier, patterns in KEYWORD_TIERS.items()
}

class PoliticalMonitor:
    def __init__(self):
        pass

    def _classify_urgency(self, text: str) -> str:
        """Classify urgency based on keyword tiers (P0 > P1 > default P2)."""
        try:
            if _COMPILED_KEYWORDS["P0"].search(text):
                return "P0"
            if _COMPILED_KEYWORDS["P1"].search(text):
                return "P1"
            return "P2"
        except Exception as e:
            logger.error(f"[PoliticalMonitor] Error classifying urgency: {e}")
            return "P2"

    def _extract_tickers(self, text: str) -> list:
        """Extract tickers from text (explicit $AAPL and mapping keywords)."""
        tickers = set()
        try:
            # 1. Standard explicit tickers starting with $
            explicit = re.findall(r"\$([A-Za-z]+)", text)
            for t in explicit:
                tickers.add(t.upper())

            # 2. Company/crypto names mapping to tickers
            mappings = {
                r"\btesla\b": "TSLA",
                r"\bdell\b": "DELL",
                r"\bbitcoin\b": "BTC",
                r"\bbtc\b": "BTC",
                r"\bapple\b": "AAPL",
                r"\bmicrosoft\b": "MSFT",
                r"\bnvidia\b": "NVDA",
                r"\bgoogle\b": "GOOGL",
                r"\bmeta\b": "META",
                r"\bamd\b": "AMD"
            }
            for pattern, ticker in mappings.items():
                if re.search(pattern, text, re.IGNORECASE):
                    tickers.add(ticker)
        except Exception as e:
            logger.error(f"[PoliticalMonitor] Error extracting tickers: {e}")
        return sorted(list(tickers))

    def _effective_urgency(self, raw_urgency: str, tier: str) -> str:
        """Escalate or cap urgency based on VIP account tier."""
        try:
            if tier == "S":
                if raw_urgency == "P1":
                    return "P0"
                if raw_urgency == "P2":
                    return "P1"
            elif tier == "B":
                if raw_urgency == "P0":
                    return "P1"
            return raw_urgency
        except Exception as e:
            logger.error(f"[PoliticalMonitor] Error calculating effective urgency: {e}")
            return raw_urgency

    def _canonical_url(self, handle: str, content: str, url: str) -> str:
        """Get or generate canonical url for tracking."""
        if url and ("x.com" in url or "twitter.com" in url):
            return url
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        return f"https://x.com/{handle}/status/hash_{content_hash}"

    def _fire_alert(self, handle: str, meta: dict, content: str, urgency: str, tickers: list):
        """Format and send political alert to the notification queue."""
        try:
            emoji = meta.get("emoji", "📢")
            display_name = meta.get("display", handle)
            tier = meta.get("tier", "B")
            
            ticker_str = ", ".join(tickers) if tickers else "None"
            
            message = (
                f"───────────────────────────\n"
                f"🚨🚨🚨 POLITICAL INTEL — {urgency}\n"
                f"───────────────────────────\n"
                f"{emoji} *{display_name}* (@{handle})\n"
                f"📊 Tier: {tier} | Priority: {urgency}\n\n"
                f"💬 {content}\n"
                f"🎯 *Tickers:* {ticker_str}\n"
                f"───────────────────────────"
            )
            
            level = "alert" if urgency == "P0" else "info"
            notification_queue.enqueue(message, level=level, category="news")
            logger.info(f"[PoliticalMonitor] Fired alert for @{handle} with urgency {urgency}")
        except Exception as e:
            logger.error(f"[PoliticalMonitor] Error firing alert: {e}")

    def get_recent_political_sentiment(self) -> dict:
        """Fetch recent posts and return aggregate sentiment score."""
        db = SessionLocal()
        try:
            time_threshold = datetime.utcnow() - timedelta(hours=24)
            posts = db.query(SocialPost).filter(
                SocialPost.platform == "X",
                SocialPost.scraped_at >= time_threshold
            ).all()
            
            political_posts = []
            for post in posts:
                if post.author in VIP_ACCOUNTS:
                    political_posts.append(post)
                    
            if not political_posts:
                return {
                    "label": "NO_DATA",
                    "score": 0.0,
                    "post_count": 0
                }
                
            total_score = 0.0
            for post in political_posts:
                score = sentiment_engine.score_text(post.content)
                total_score += score
                
            avg_score = total_score / len(political_posts)
            label = "NEUTRAL"
            if avg_score >= 0.15:
                label = "BULLISH"
            elif avg_score <= -0.15:
                label = "BEARISH"
                
            return {
                "label": label,
                "score": avg_score,
                "post_count": len(political_posts)
            }
        except Exception as e:
            logger.error(f"[PoliticalMonitor] Error computing sentiment: {e}")
            return {
                "label": "ERROR",
                "score": 0.0,
                "post_count": 0
            }
        finally:
            db.close()

    def get_recent_ticker_tweets(self, ticker: str, hours: int = 24) -> list:
        """
        Return tweets from VIP handles in the last `hours` that mention `ticker`
        or its associated company name.

        Returns list of dicts: {handle, tier, timestamp, content, sentiment}
        """
        db = SessionLocal()
        try:
            time_threshold = datetime.utcnow() - timedelta(hours=hours)
            posts = db.query(SocialPost).filter(
                SocialPost.platform == "X",
                SocialPost.scraped_at >= time_threshold
            ).all()

            monitored_handles = set(VIP_ACCOUNTS.keys())

            results = []
            for post in posts:
                if post.author not in monitored_handles:
                    continue

                # Check if this post mentions the ticker
                if matches_ticker(post.content, ticker):
                    sentiment = _get_sentiment(post.content)
                    results.append({
                        "handle": post.author,
                        "tier": get_tier(post.author),
                        "timestamp": post.posted_at.isoformat() if post.posted_at else post.scraped_at.isoformat(),
                        "content": post.content,
                        "sentiment": sentiment
                    })
            return results
        except Exception as e:
            logger.error(f"[PoliticalMonitor] Error in get_recent_ticker_tweets: {e}")
            return []
        finally:
            db.close()

    def run(self):
        """Placeholder execution logic."""
        logger.info("[PoliticalMonitor] Run started.")

political_monitor = PoliticalMonitor()
