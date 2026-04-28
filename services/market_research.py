from loguru import logger
from datetime import datetime
from core.database import SessionLocal, NewsIntel, SocialPost
from services.moomoo_service import moomoo_service
from services.technical_analysis import ta_service
from services.earnings_calendar import earnings_service
from services.options_flow import options_service
from services.ai_service import ai_service

class MarketResearchService:
    def perform_deep_dive(self, symbol: str) -> str:
        """Perform a comprehensive research report on a stock."""
        symbol = symbol.upper()
        logger.info(f"[Research] Starting deep-dive for {symbol}...")
        
        # 1. Get Technical Analysis
        ta = ta_service.get_full_analysis(symbol)
        
        # 2. Get Earnings Info
        earnings = earnings_service.get_stock_earnings(symbol)
        
        # 3. Get Options Flow
        options = options_service.get_unusual_activity(symbol)
        
        # 4. Get recent News & Social from DB
        db = SessionLocal()
        news = db.query(NewsIntel).filter(NewsIntel.headline.ilike(f"%{symbol}%")).limit(3).all()
        social = db.query(SocialPost).filter(SocialPost.content.ilike(f"%{symbol}%")).limit(3).all()
        db.close()
        
        # 5. Build context for AI
        context = (
            f"STOCK: {symbol}\n"
            f"PRICE: ${ta.get('price', 'N/A')}\n"
            f"TECHNICALS: RSI {ta.get('rsi')}, Trend {ta.get('summary')}\n"
            f"EARNINGS: {earnings.get('earnings_dates', ['Unknown'])[0]}\n"
            f"OPTIONS: PCR {options.get('put_call_ratio', 'N/A')}, Sentiment {options.get('sentiment')}\n"
            f"RECENT NEWS: " + ("; ".join([n.headline for n in news]) if news else "None found") + "\n"
            f"SOCIAL CHATTER: " + ("; ".join([s.content[:50] + "..." for s in social]) if social else "Quiet")
        )
        
        prompt = (
            f"You are the Lead Analyst at MooPredict AI.\n"
            f"Perform a comprehensive 'Deep Dive' report for the ticker {symbol}.\n"
            f"Analyze the technicals, options sentiment, and news context provided below.\n"
            f"Provide a clear 'Verdict' (BULLISH/BEARISH/NEUTRAL) and actionable reasoning.\n"
            f"Be concise, use formatting and emojis.\n\n"
            f"DATA:\n{context}"
        )
        
        report = ai_service.query(prompt)
        
        final_report = (
            f"🔍 *DEEP DIVE: {symbol}*\n"
            f"───────────────────────────\n"
            f"{report}\n\n"
            f"⚠️ *Disclaimer:* AI analysis is for informational purposes only."
        )
        
        return final_report

research_service = MarketResearchService()
