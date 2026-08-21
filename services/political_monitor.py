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

TICKER_ALIASES = {
    # ─── SINGLE STOCKS (2026-08-14, audit fix: consumer news writes "Nvidia", never
    # "NVDA" — 0/519 headlines contained a ticker string; company names are how news
    # actually references these) ──────────────────────────────────────────────
    "NVDA":  ["Nvidia"],
    "AAPL":  ["Apple"],
    "MRVL":  ["Marvell"],
    "NOK":   ["Nokia"],
    "STX":   ["Seagate"],
    "SKHY":  ["SK Hynix", "SK hynix"],
    "AMD":   ["Advanced Micro Devices"],
    "INTC":  ["Intel"],
    "MSFT":  ["Microsoft"],
    "GOOGL": ["Google", "Alphabet"],
    "AMZN":  ["Amazon"],
    "META":  ["Meta Platforms", "Facebook"],
    "TSLA":  ["Tesla"],
    "IBM":   ["IBM"],
    # ─── ETF UNIVERSE (primary focus as of 2026-06-26) ────────────────────────
    # Broad index ETFs
    "SPY":   ["S&P 500", "SPX", "S and P 500", "broad market", "US stocks"],
    "QQQ":   ["Nasdaq 100", "Nasdaq", "tech-heavy index", "QQQs"],
    "DIA":   ["Dow Jones", "Dow 30", "blue chips"],
    "IWM":   ["Russell 2000", "small caps", "small-cap"],
    # Sector ETFs (SPDR family)
    "XLK":   ["tech sector", "technology sector", "tech ETF"],
    "XLE":   ["energy sector", "oil and gas sector", "energy ETF"],
    "XLF":   ["financial sector", "banks ETF", "financials"],
    "XLV":   ["healthcare sector", "health ETF"],
    "XLI":   ["industrials sector", "industrial ETF"],
    "XLP":   ["consumer staples", "staples ETF"],
    "XLY":   ["consumer discretionary", "discretionary ETF"],
    "XLU":   ["utilities sector", "utilities ETF"],
    "XLB":   ["materials sector", "materials ETF"],
    "XLRE":  ["real estate sector", "REIT ETF"],
    "XLC":   ["communications sector", "communication services"],
    # Thematic / semiconductor ETFs
    "SOXX":  ["semiconductor ETF", "chip ETF", "semis", "chipmaker", "chip stocks", "SK Hynix", "Samsung Electronics"],
    "SMH":   ["semiconductor ETF", "VanEck Semi", "chip ETF", "chipmaker", "SK Hynix", "Samsung Electronics"],
    "XBI":   ["biotech ETF", "biotechnology sector"],
    "DRAM":  ["memory chip ETF", "DRAM ETF", "Roundhill Memory", "SK Hynix", "memory chip", "Kioxia", "SanDisk"],
    "ARKK":  ["ARK Innovation", "Cathie Wood ETF", "disruptive innovation"],
    # ─── LEVERAGED / INVERSE ETFs (added 2026-07-02 — aggressive paper universe) ──
    # Index 3x bulls
    "TQQQ":  ["3x Nasdaq", "leveraged QQQ", "3x tech", "TQQQ"],
    "UPRO":  ["3x SPY", "leveraged S&P 500", "UPRO"],
    "TNA":   ["3x Russell 2000", "leveraged small caps", "TNA"],
    "SPXL":  ["3x SPY Direxion", "SPXL"],
    # Index 3x bears
    "SQQQ":  ["inverse Nasdaq", "short QQQ", "3x bear Nasdaq", "SQQQ"],
    "SPXU":  ["inverse SPY", "3x bear S&P", "SPXU"],
    "TZA":   ["inverse Russell 2000", "3x bear small caps", "TZA"],
    "SPXS":  ["inverse SPY Direxion", "SPXS"],
    # Index 2x bulls
    "SSO":   ["2x SPY", "SSO"],
    "QLD":   ["2x QQQ", "QLD"],
    # Sector 3x bulls
    "TECL":  ["3x tech sector", "leveraged XLK", "TECL"],
    "ERX":   ["3x energy", "leveraged XLE", "ERX"],
    "FAS":   ["3x financials", "leveraged XLF", "FAS"],
    "DPST":  ["3x regional banks", "DPST"],
    "LABU":  ["3x biotech", "leveraged XBI", "LABU"],
    "NAIL":  ["3x homebuilders", "NAIL"],
    # Sector 3x bears
    "TECS":  ["inverse tech sector", "3x bear XLK", "TECS"],
    "ERY":   ["inverse energy", "3x bear XLE", "ERY"],
    "FAZ":   ["inverse financials", "3x bear XLF", "FAZ"],
    "LABD":  ["inverse biotech", "3x bear XBI", "LABD"],
    # Single-stock 2x bulls (GraniteShares / Direxion)
    "NVDL":  ["2x Nvidia", "leveraged NVDA long", "NVDL"],
    "MUU":   ["2x Micron", "leveraged MU long", "MUU"],
    "TSLL":  ["2x Tesla", "leveraged TSLA long", "TSLL"],
    "AAPU":  ["2x Apple", "leveraged AAPL long", "AAPU"],
    "MSFL":  ["2x Microsoft", "leveraged MSFT long", "MSFL"],
    "METU":  ["2x Meta", "leveraged META long", "METU"],
    "AMZU":  ["2x Amazon", "leveraged AMZN long", "AMZU"],
    "GGLL":  ["2x Google", "leveraged GOOGL long", "GGLL"],
    "AMDL":  ["2x AMD", "leveraged AMD long", "AMDL"],
    # Single-stock 2x bears
    "NVDS":  ["inverse Nvidia", "short NVDA 2x", "NVDS"],
    "MUD":   ["inverse Micron", "short MU 2x", "MUD"],
    "TSLQ":  ["inverse Tesla", "short TSLA 2x", "TSLQ"],
    "TSLS":  ["inverse Tesla Direxion", "TSLS"],
    "AAPD":  ["inverse Apple", "short AAPL 2x", "AAPD"],
    "MSFD":  ["inverse Microsoft", "short MSFT 2x", "MSFD"],
    "METD":  ["inverse Meta", "short META 2x", "METD"],
    "AMZD":  ["inverse Amazon", "short AMZN 2x", "AMZD"],
    "GGLS":  ["inverse Google", "short GOOGL 2x", "GGLS"],
    "AMDS":  ["inverse AMD", "short AMD 2x", "AMDS"],
    # Commodity ETFs
    "GLD":   ["gold ETF", "physical gold"],
    "SLV":   ["silver ETF"],
    "USO":   ["oil ETF", "crude oil ETF"],
    # ─── INDIVIDUAL STOCKS (legacy — kept for backfilling history, no new picks) ──
    "GOOGL": ["Google", "Alphabet", "Gemma", "Bard", "Pichai", "Pixel", "YouTube", "DeepMind"],
    "GOOG":  ["Google", "Alphabet", "Gemma", "Bard", "Pichai", "Pixel", "YouTube", "DeepMind"],
    "NVDA":  ["Nvidia", "Jensen", "Huang", "CUDA", "Blackwell", "Hopper", "RTX"],
    "AAPL":  ["Apple", "iPhone", "iPad", "Tim Cook", "Cook"],
    "META":  ["Meta", "Facebook", "Instagram", "WhatsApp", "Zuckerberg", "Zuck", "Threads"],
    "TSLA":  ["Tesla", "Elon", "Musk", "Cybertruck", "Robotaxi", "FSD"],
    "MSFT":  ["Microsoft", "Satya", "Nadella", "Azure", "Copilot"],
    "AMZN":  ["Amazon", "AWS", "Bezos", "Andy Jassy"],
    "MARA":  ["Marathon Digital", "Marathon"],
    "CVX":   ["Chevron"],
    "INTC":  ["Intel", "Pat Gelsinger", "Lip-Bu Tan", "18A", "Foundry"],
    "MRVL":  ["Marvell"],
    "AMD":   ["AMD", "Advanced Micro Devices", "Lisa Su", "Ryzen", "Radeon"],
    "NOK":   ["Nokia"],
    "SMCI":  ["Super Micro", "Supermicro"],
    "UEC":   ["Uranium Energy"],
    "ROKU":  ["Roku"],
    "DELL":  ["Dell"],
    "URA":   ["Uranium ETF"],
}

def matches_ticker(text: str, ticker: str) -> bool:
    """Check if the text mentions a ticker symbol or its aliases."""
    try:
        ticker = ticker.upper()
        # Check direct $TICKER mention (e.g. $AAPL)
        if re.search(r"\$" + re.escape(ticker) + r"\b", text, re.IGNORECASE):
            return True
        
        # Check ticker symbol with word boundaries (e.g. \bAAPL\b)
        if re.search(r"\b" + re.escape(ticker) + r"\b", text, re.IGNORECASE):
            return True
        
        # Check aliases
        aliases = TICKER_ALIASES.get(ticker, [])
        for alias in aliases:
            pattern = r"\b" + re.escape(alias) + r"\b"
            if re.search(pattern, text, re.IGNORECASE):
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
