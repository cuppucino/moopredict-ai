from datetime import datetime, timedelta
from typing import List, Dict
from loguru import logger

class MacroCalendarService:
    def __init__(self):
        # Simulated high-impact events for demonstration. 
        # In production, this could fetch from AlphaVantage, FMP, or a scraper.
        self.events = [
            {"event": "FOMC Interest Rate Decision", "impact": "HIGH", "date": "2026-05-20", "time": "18:00 UTC"},
            {"event": "CPI (Consumer Price Index) YoY", "impact": "HIGH", "date": "2026-05-14", "time": "12:30 UTC"},
            {"event": "Non-Farm Payrolls (NFP)", "impact": "HIGH", "date": "2026-06-05", "time": "12:30 UTC"},
            {"event": "OPEC+ Meeting", "impact": "MEDIUM", "date": "2026-05-25", "time": "09:00 UTC"},
            {"event": "GDP Growth Rate QoQ", "impact": "HIGH", "date": "2026-05-28", "time": "12:30 UTC"}
        ]

    def get_upcoming_events(self, days: int = 7) -> List[Dict]:
        """Get events occurring within the next N days."""
        upcoming = []
        now = datetime.now()
        horizon = now + timedelta(days=days)
        
        for e in self.events:
            try:
                e_date = datetime.strptime(e["date"], "%Y-%m-%d")
                if now <= e_date <= horizon:
                    upcoming.append(e)
            except Exception as ex:
                logger.error(f"[Macro] Date parse error for {e['event']}: {ex}")
        
        return sorted(upcoming, key=lambda x: x["date"])

    def generate_weekly_briefing(self) -> str:
        """Generate a formatted briefing for the upcoming week."""
        upcoming = self.get_upcoming_events(7)
        if not upcoming:
            return "📅 *Macro Calendar*: No high-impact events scheduled for the coming week."
            
        lines = ["🗓️ *Weekly Macro Calendar*"]
        for e in upcoming:
            impact_emoji = "🔴" if e["impact"] == "HIGH" else "🟡"
            lines.append(f"{impact_emoji} *{e['event']}*")
            lines.append(f"  • Date: {e['date']} | {e['time']}")
            
        return "\n".join(lines)

macro_service = MacroCalendarService()
