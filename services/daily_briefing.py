from datetime import datetime
import time
from typing import Dict, List, Optional
from loguru import logger
from core.database import SessionLocal, NewsIntel, SocialPost
from services.moomoo_service import moomoo_service
from services.sector_analysis import sector_service
from services.ai_service import ai_service
from services.notifications import notification_queue
from services.technical_analysis import ta_service

class DailyBriefingService:
    def generate_morning_briefing(self):
        """10:00 AM MYT - Overnight recap and market setup."""
        logger.info("[Briefing] Generating Morning Briefing...")
        db = SessionLocal()
        try:
            # 1. Get overnight news (last 12 hours)
            news = db.query(NewsIntel).order_by(NewsIntel.scraped_at.desc()).limit(5).all()
            news_lines = [f"- [{n.source}] {n.headline}" for n in news]
            
            # 2. Get social sentiment
            social = db.query(SocialPost).order_by(SocialPost.scraped_at.desc()).limit(5).all()
            social_lines = [f"- @{s.author} ({s.platform}): {s.content[:100]}..." for s in social]
            
            # 3. Get sector status
            sectors = sector_service.get_sector_performance()[:3] # Top 3
            sector_lines = [f"- {s['name']}: {s['change_1d']}%" for s in sectors]
            
            # 4. Use AI to synthesize
            context = f"NEWS:\n" + "\n".join(news_lines) + "\n\nSOCIAL:\n" + "\n".join(social_lines) + "\n\nSECTORS:\n" + "\n".join(sector_lines)
            
            prompt = (
                "You are MooPredict AI. Provide a 'Morning Briefing' (10:00 AM MYT).\n"
                "Summarize overnight US market action and top catalysts for the day ahead.\n"
                "Be concise, professional, and use emojis. Focus on uranium, tech, and energy.\n\n"
                f"DATA:\n{context}"
            )
            
            report = ai_service.query(prompt)
            
            # 5. Format final message
            message = (
                f"☀️ *MORNING INTELLIGENCE — {datetime.now().strftime('%H:%M')}*\n"
                f"───────────────────────────\n"
                f"{report}\n\n"
                f"💡 *Tip:* Use !ta SYMBOL for specific deep-dives."
            )
            
            notification_queue.enqueue(message)
            logger.info("[Briefing] Morning Briefing sent to queue.")
            
        finally:
            db.close()

    def generate_premarket_prep(self):
        """04:00 PM MYT - Technical review and upcoming earnings."""
        logger.info("[Briefing] Generating Pre-Market Prep...")
        # Get positions
        positions = moomoo_service.get_positions()
        pos_lines = []
        for p in positions:
            ta = ta_service.get_full_analysis(p['symbol'])
            pos_lines.append(f"• {p['symbol']}: RSI {ta.get('rsi', 'N/A')} | {ta.get('summary', 'NEUTRAL')}")
            
        message = (
            f"☕ *PRE-MARKET PREP*\n"
            f"───────────────────────────\n"
            f"*Current Positions:* \n" + "\n".join(pos_lines) + "\n\n"
            f"📈 Watch for volatility at 9:30 PM open."
        )
        notification_queue.enqueue(message)

    def generate_eod_summary(self):
        """04:00 AM MYT - End of day performance recap."""
        logger.info("[Briefing] Generating EOD Summary...")
        balance = moomoo_service.get_balance()
        assets = f"${balance['total_assets']:,.2f}" if balance else "N/A"
        
        message = (
            f"🌑 *MARKET CLOSE SUMMARY*\n"
            f"───────────────────────────\n"
            f"💰 Portfolio Value: {assets}\n"
            f"📊 Check !pos for full P&L breakdown."
        )
        notification_queue.enqueue(message)

    def get_briefing_data(self, quick: bool = False) -> Dict:
        """Fetch all data components for a briefing. If quick=True, skip AI summary."""
        db = SessionLocal()
        start_time = time.time()
        try:
            # 1. Get overnight news (last 12 hours)
            news = db.query(NewsIntel).order_by(NewsIntel.scraped_at.desc()).limit(5).all()
            news_data = [{"source": n.source, "headline": n.headline, "url": n.url} for n in news]
            t1 = time.time()
            logger.debug(f"[Briefing] News fetch took {t1 - start_time:.3f}s")
            
            # 2. Get social sentiment
            social = db.query(SocialPost).order_by(SocialPost.scraped_at.desc()).limit(5).all()
            social_data = [{"platform": s.platform, "author": s.author, "content": s.content} for s in social]
            t2 = time.time()
            logger.debug(f"[Briefing] Social fetch took {t2 - t1:.3f}s")
            
            # 3. Get sector status
            sectors = sector_service.get_sector_performance()[:5]
            t3 = time.time()
            logger.debug(f"[Briefing] Sector fetch took {t3 - t2:.3f}s")
            
            # 4. Get portfolio summary
            positions = moomoo_service.get_positions()
            balance = moomoo_service.get_balance()
            t4 = time.time()
            logger.debug(f"[Briefing] Portfolio fetch took {t4 - t3:.3f}s")
            
            # 5. Generate AI Summary (cached or new) - SKIP if quick=True
            ai_summary = "AI summary skipped (Quick Mode)"
            if not quick:
                news_lines = [f"- {n.headline}" for n in news]
                context = "NEWS:\n" + "\n".join(news_lines)
                prompt = f"Summarize these market catalysts briefly for a professional trader:\n{context}"
                ai_summary = ai_service.query(prompt) if news_lines else "No significant news to summarize."
                t5 = time.time()
                logger.debug(f"[Briefing] AI Summary took {t5 - t4:.3f}s")
            
            logger.info(f"[Briefing] Data retrieval complete in {time.time() - start_time:.3f}s")
            return {
                "timestamp": datetime.now().isoformat(),
                "portfolio": {
                    "balance": balance,
                    "positions": positions
                },
                "sectors": sectors,
                "news": news_data,
                "social": social_data,
                "ai_summary": ai_summary,
                "mode": "QUICK" if quick else "FULL"
            }
        finally:
            db.close()

briefing_service = DailyBriefingService()
