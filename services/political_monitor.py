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
        "impact": "Market-wide, geopolitics, tariffs, crypto",
        "emoji": "🇺🇸"
    },
    "elonmusk": {
        "display": "Elon Musk",
        "tier": "S",
        "impact": "Crypto, tech, TSLA, DOGE",
        "emoji": "🚀"
    },
    "federalreserve": {
        "display": "Federal Reserve",
        "tier": "A",
        "impact": "Rates, bonds, market-wide",
        "emoji": "🏦"
    },
    "SecYellen": {
        "display": "Treasury Secretary",
        "tier": "A",
        "impact": "Fiscal, bonds, dollar",
        "emoji": "💵"
    },
    "SECGov": {
        "display": "SEC",
        "tier": "A",
        "impact": "Regulation, crypto, enforcement",
        "emoji": "⚖️"
    },
    "GaryGensler": {
        "display": "Gary Gensler",
        "tier": "A",
        "impact": "Crypto, regulation",
        "emoji": "🏛️"
    },
    "WhiteHouse": {
        "display": "White House",
        "tier": "A",
        "impact": "Policy, executive orders",
        "emoji": "🏛️"
    },
    "CathieDWood": {
        "display": "Cathie Wood",
        "tier": "B",
        "impact": "Growth, tech, ARKK",
        "emoji": "📈"
    },
    "saylor": {
        "display": "Michael Saylor",
        "tier": "B",
        "impact": "Crypto, BTC, MSTR",
        "emoji": "🪙"
    }
}

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
                r"\bmicrosoft\b": "MSFT"
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

    def run(self):
        """Placeholder execution logic."""
        logger.info("[PoliticalMonitor] Run started.")

political_monitor = PoliticalMonitor()
