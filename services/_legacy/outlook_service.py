from datetime import datetime, timedelta
import json
from typing import Dict, List, Optional, Any
from loguru import logger
from services.moomoo_service import moomoo_service
from services.sector_analysis import sector_service
from services.prediction_service import prediction_service
from services.earnings_calendar import earnings_service
from services.ai_service import ai_service
from services.notifications import notification_queue
from core.database import SessionLocal, UserWatchlist

class OutlookService:
    def __init__(self):
        self._cache = None
        self._cache_expiry = datetime.now()

    def generate_weekly_outlook(self, force: bool = False) -> str:
        """
        Generate a highly structured weekly outlook report using AI for data analysis
        and Python for guaranteed table formatting.
        """
        if self._cache and datetime.now() < self._cache_expiry and not force:
            logger.info("[Outlook] Returning cached weekly outlook.")
            return self._cache

        logger.info("[Outlook] Generating Weekly Outlook (JSON-to-Markdown)...")
        try:
            # 1. Gather Rich Data
            sectors = sector_service.get_sector_performance()
            balance = moomoo_service.get_balance()
            positions = moomoo_service.get_positions()
            
            db = SessionLocal()
            watchlist_items = db.query(UserWatchlist).all()
            watchlist_symbols = [item.symbol for item in watchlist_items]
            db.close()
            
            active_preds = prediction_service.get_active(limit=10)
            pred_stats = prediction_service.get_stats()
            total_resolved = pred_stats.get('total_resolved', 0)
            
            health_status = "Initial" if total_resolved < 10 else "Established"
            
            all_symbols = list(set(watchlist_symbols + [p['symbol'] for p in positions]))
            earnings = earnings_service.get_watchlist_earnings(all_symbols)
            
            # 2. Context Preparation
            context = {
                "sectors": sectors,
                "portfolio": {
                    "balance": balance,
                    "positions": positions
                },
                "predictions": {
                    "stats": pred_stats,
                    "active": active_preds,
                    "health": health_status
                },
                "earnings": [e for e in earnings if e.get("success") and e.get("earnings_dates")]
            }

            # 3. AI Analysis (JSON Output)
            prompt = (
                "You are MooPredict AI. Analyze the market data and output a structured JSON report.\n"
                "REQUIRED JSON STRUCTURE:\n"
                "{\n"
                "  'health_table': [{'Problem': 'string', 'Impact': 'string', 'Solution': 'string'}],\n"
                "  'sector_table': [{'Sector': 'string', 'Outlook': 'string', 'Watch': 'string'}],\n"
                "  'high_interest_table': [{'Stock': 'string', 'Why': 'string', 'Catalyst': 'string', 'Confidence': 'string'}],\n"
                "  'watchlist_table': [{'Stock': 'string', 'Sector': 'string', 'Catalyst': 'string', 'EntryStrategy': 'string'}],\n"
                "  'earnings_play': {'Ticker': 'string', 'Scenarios': [{'Scenario': 'string', 'Probability': 'string', 'Action': 'string'}]},\n"
                "  'action_items': {'Me': [{'Task': 'string', 'Priority': 'string'}], 'System': [{'Task': 'string', 'Priority': 'string'}], 'You': [{'Task': 'string', 'Priority': 'string'}]}\n"
                "}\n\n"
                f"DATA SOURCE:\n{json.dumps(context)}"
            )
            
            analysis = ai_service.query_json(prompt)
            logger.debug(f"[Outlook] AI Analysis Result: {json.dumps(analysis)[:500]}...")
            
            # 4. Post-Process: Render Markdown Tables
            report = f"🌟 **WEEKLY OUTLOOK — {datetime.now().strftime('%Y-%m-%d')}**\n"
            report += "───────────────────────────\n\n"

            # Section 1: Health
            report += "### 1. TRACK RECORD & SYSTEM HEALTH\n"
            report += self._render_table(["Problem", "Impact", "Solution"], analysis.get("health_table", []))
            report += "\n"

            # Section 2: Sector Leadership
            sector_rows = []
            ai_sectors = analysis.get("sector_table", [])
            # Map AI sectors by symbol for easier lookup
            ai_sector_map = {}
            for item in ai_sectors:
                if isinstance(item, dict):
                    name = str(item.get("Sector", "")).upper()
                    ai_sector_map[name] = item
                elif isinstance(item, list) and len(item) > 0:
                    name = str(item[0]).upper()
                    ai_sector_map[name] = item

            for s in sectors[:8]: # Top 8 sectors
                symbol = s['symbol']
                ai_data = ai_sector_map.get(symbol) or ai_sector_map.get(s['name'].upper())
                
                outlook = "Neutral"
                watch = "Observe"
                
                if isinstance(ai_data, dict):
                    outlook = ai_data.get("Outlook", "Neutral")
                    watch = ai_data.get("Watch", "Observe")
                elif isinstance(ai_data, list):
                    outlook = ai_data[1] if len(ai_data) > 1 else "Neutral"
                    watch = ai_data[2] if len(ai_data) > 2 else "Observe"
                    
                sector_rows.append({
                    "Sector": f"{s['name']} ({symbol})",
                    "1W Change": f"{s['change_1w']}%",
                    "Outlook": outlook,
                    "Watch": watch
                })
            report += "### 2. SECTOR LEADERSHIP DATA\n"
            report += self._render_table(["Sector", "1W Change", "Outlook", "Watch"], sector_rows)
            report += "\n"

            # Section 3: High Interest
            report += "📈 **STOCKS TO WATCH NEXT WEEK**\n\n"
            report += "**HIGH INTEREST**\n"
            report += self._render_table(["Stock", "Why", "Catalyst", "Confidence"], analysis.get("high_interest_table", []))
            report += "\n"

            # Section 4: Watchlist
            report += "**WATCHLIST FOR NEXT WEEK**\n"
            report += self._render_table(["Stock", "Sector", "Catalyst", "Entry Strategy"], analysis.get("watchlist_table", []))
            report += "\n"

            # Section 5: Earnings Play
            ep = analysis.get("earnings_play", {})
            if ep:
                report += f"**{ep.get('Ticker', 'MARKET')} EARNINGS PLAY**\n"
                report += self._render_table(["Scenario", "Probability", "Action"], ep.get("Scenarios", []))
                report += "\n"

            # Section 6: Action Items
            report += "🎯 **ACTION ITEMS FOR NEXT WEEK**\n"
            ai_items = analysis.get("action_items", {})
            
            report += "**FOR ME**\n"
            report += self._render_table(["Task", "Priority"], ai_items.get("Me", []))
            report += "\n"

            report += "**FOR THE SYSTEM**\n"
            report += self._render_table(["Task", "Priority"], ai_items.get("System", []))
            report += "\n"

            report += "**FOR YOU**\n"
            report += self._render_table(["Task", "Priority"], ai_items.get("You", []))
            report += "\n"

            report += "📈 *Stay disciplined. Respect the risk.*"

            # 5. Finalize
            notification_queue.enqueue(report, category="news")
            self._cache = report
            self._cache_expiry = datetime.now() + timedelta(hours=1)
            
            logger.info("[Outlook] Weekly outlook generated and sent.")
            return report
            
        except Exception as e:
            logger.error(f"[Outlook] Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return f"⚠️ Outlook Error: {str(e)}"

    def _render_table(self, headers: List[str], rows: List[Any]) -> str:
        """Helper to render a markdown table from a list of dicts or lists."""
        if not rows:
            return "_No data available._\n"
            
        normalized_rows = []
        for row in rows:
            normalized_row = {}
            if isinstance(row, dict):
                for h in headers:
                    val = row.get(h)
                    if val is None:
                        # Look for case-insensitive key
                        for k, v in row.items():
                            if k.lower().replace(" ", "") == h.lower().replace(" ", ""):
                                val = v
                                break
                    normalized_row[h] = val if val is not None else "N/A"
            elif isinstance(row, list):
                for i, h in enumerate(headers):
                    normalized_row[h] = row[i] if i < len(row) else "N/A"
            else:
                # Handle single strings or other types if AI messes up
                normalized_row = {h: str(row) if i == 0 else "N/A" for i, h in enumerate(headers)}
                
            normalized_rows.append(normalized_row)

        header_line = "| " + " | ".join(headers) + " |"
        sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
        
        body_lines = []
        for row in normalized_rows:
            line = "| " + " | ".join([str(row.get(h, "N/A")) for h in headers]) + " |"
            body_lines.append(line)
            
        return "\n".join([header_line, sep_line] + body_lines) + "\n"

outlook_service = OutlookService()
