from datetime import datetime
from loguru import logger
from core.database import SessionLocal, DailyPerformance, PaperTrade
from services.notifications import notification_queue
from services.ai_service import ai_service
from services.paper_trading import paper_trading_service

class ReportService:
    def generate_report(self) -> str:
        db = SessionLocal()
        try:
            today = datetime.utcnow().date()
            
            trades = db.query(PaperTrade).filter(
                PaperTrade.status != "OPEN",
                PaperTrade.closed_at >= datetime(today.year, today.month, today.day)
            ).all()
            
            wins = sum(1 for t in trades if t.outcome == "WIN")
            losses = sum(1 for t in trades if t.outcome == "LOSS")
            session_pnl = sum(t.pnl_amount for t in trades if t.pnl_amount)
            
            perf = paper_trading_service.get_performance()
            total_balance = perf.get("current_balance", 257.64)
            win_rate = (wins / len(trades) * 100) if trades else 0.0
            
            dp = db.query(DailyPerformance).filter(DailyPerformance.date == today).first()
            if not dp:
                dp = DailyPerformance(date=today)
                db.add(dp)
            
            dp.trades_won = wins
            dp.trades_lost = losses
            dp.session_pnl = session_pnl
            dp.cumulative_pnl = perf.get("total_pnl_usd", 0)
            dp.capital_end = total_balance
            dp.win_rate = win_rate
            db.commit()
            
            trade_summaries = [f"{t.symbol} ({t.side}): {t.outcome} {t.pnl_percent:.2f}%" for t in trades]
            if trade_summaries:
                ai_prompt = f"Summarize today's trading session. Trades: {', '.join(trade_summaries)}. Keep it under 3 sentences, professional tone."
                session_summary = ai_service.query(ai_prompt)
            else:
                session_summary = "No trades closed today."

            report = (
                f"📊 *MooPredict AI Daily Report*\n"
                f"Date: {today.strftime('%Y-%m-%d')}\n\n"
                f"📈 *Session Performance*\n"
                f"• P&L: ${session_pnl:+.2f}\n"
                f"• Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n\n"
                f"💰 *Account Status*\n"
                f"• Balance: ${total_balance:.2f}\n"
                f"• Total Return: {perf.get('total_return_pct', 0):+.2f}%\n\n"
                f"🤖 *AI Summary*\n"
                f"{session_summary}"
            )
            return report
        except Exception as e:
            logger.error(f"[ReportService] Error generating report: {e}")
            db.rollback()
            return "⚠️ Error generating report."
        finally:
            db.close()

    def send_report(self):
        report = self.generate_report()
        notification_queue.enqueue(report, level="info", category="general")

report_service = ReportService()
