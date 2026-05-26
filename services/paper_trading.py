"""
Paper Trading Service for MooPredict.

Tracks simulated trades in a database, manages starting capital,
enforces stop loss and take profit targets, and exposes portfolio metrics.
"""

from datetime import datetime
from loguru import logger

from core.database import SessionLocal, PaperTrade
from services.moomoo_service import moomoo_service

class PaperTradingService:
    def __init__(self, starting_capital=257.64):
        self.starting_capital = starting_capital
        self.check_count = 0

    def open_trade(self, symbol: str, side: str, quantity: float, price: float, 
                   stop_loss: float = None, take_profit: float = None, 
                   strategy: str = "MANUAL", reasoning: str = None, 
                   catalyst: str = None, decision_id: int = None, 
                   news_context: str = None) -> dict:
        """Open a new simulated paper trade with mandatory risk gates."""
        db = SessionLocal()
        try:
            # Enforce automatic risk thresholds if none specified
            # Buy side: stop loss at -5%, target at +6%
            # Short side: stop loss at +5%, target at -6%
            if side == "BUY":
                if stop_loss is None:
                    stop_loss = round(price * 0.95, 2)
                if take_profit is None:
                    take_profit = round(price * 1.08, 2)
            else:  # SHORT
                if stop_loss is None:
                    stop_loss = round(price * 1.05, 2)
                if take_profit is None:
                    take_profit = round(price * 0.92, 2)

            trade = PaperTrade(
                symbol=symbol.upper(),
                side=side.upper(),
                quantity=float(quantity),
                entry_price=float(price),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                status="OPEN",
                strategy=strategy,
                reasoning=reasoning,
                catalyst=catalyst,
                decision_id=decision_id,
                news_context=news_context,
                opened_at=datetime.utcnow()
            )
            db.add(trade)
            db.commit()
            db.refresh(trade)
            
            logger.info(f"[PaperTrade] Opened #{trade.id} {side} {quantity} {symbol} @ {price} | SL: {stop_loss}, TP: {take_profit}")
            return {
                "success": True,
                "trade_id": trade.id,
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }
        except Exception as e:
            db.rollback()
            logger.error(f"[PaperTrade] Error opening trade: {e}")
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def close_trade(self, trade_id: int, price: float, reason: str = "manual") -> dict:
        """Close an open paper trade, calculate P&L, and update records."""
        db = SessionLocal()
        try:
            trade = db.query(PaperTrade).filter(PaperTrade.id == trade_id).first()
            if not trade:
                return {"success": False, "error": "Trade not found"}
            
            if trade.status != "OPEN":
                return {"success": False, "error": "Trade is already closed"}

            entry = trade.entry_price
            qty = trade.quantity
            
            # P&L Calculation
            if trade.side == "BUY":
                pnl_amount = (price - entry) * qty
                pnl_percent = ((price - entry) / entry) * 100
            else:  # SHORT
                pnl_amount = (entry - price) * qty
                pnl_percent = ((entry - price) / entry) * 100

            outcome = "BREAKEVEN"
            if pnl_amount > 0:
                outcome = "WIN"
            elif pnl_amount < 0:
                outcome = "LOSS"

            trade.exit_price = float(price)
            trade.pnl_amount = float(pnl_amount)
            trade.pnl_percent = float(pnl_percent)
            trade.outcome = outcome
            trade.status = "CLOSED" if reason == "manual" else reason.upper()
            trade.closed_at = datetime.utcnow()
            
            db.commit()
            logger.info(f"[PaperTrade] Closed #{trade.id} {trade.symbol} @ {price} | Outcome: {outcome} | P&L: ${pnl_amount:+.2f} ({pnl_percent:+.2f}%)")
            
            try:
                from services.lesson_writer import lesson_writer
                lesson_writer.write_lesson(trade_id)
            except Exception as e:
                logger.error(f"[PaperTrade] Failed to trigger lesson writer: {e}")
                
            return {
                "success": True,
                "pnl_pct": pnl_percent,
                "pnl_amount": pnl_amount,
                "outcome": outcome
            }
        except Exception as e:
            db.rollback()
            logger.error(f"[PaperTrade] Error closing trade #{trade_id}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def get_portfolio(self) -> list:
        """Fetch all currently open paper positions with real-time valuation."""
        db = SessionLocal()
        portfolio = []
        try:
            trades = db.query(PaperTrade).filter(PaperTrade.status == "OPEN").all()
            for t in trades:
                quote = moomoo_service.get_stock_quote(t.symbol)
                current_price = quote.get("last_price", t.entry_price) if quote else t.entry_price
                
                if t.side == "BUY":
                    pnl_pct = ((current_price - t.entry_price) / t.entry_price) * 100
                else:
                    pnl_pct = ((t.entry_price - current_price) / t.entry_price) * 100

                portfolio.append({
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "entry": t.entry_price,
                    "current": current_price,
                    "pnl_pct": pnl_pct,
                    "stop_loss": t.stop_loss,
                    "take_profit": t.take_profit
                })
        except Exception as e:
            logger.error(f"[PaperTrade] Error building portfolio: {e}")
        finally:
            db.close()
        return portfolio

    def get_performance(self) -> dict:
        """Fetch paper trading statistics and aggregated performance."""
        db = SessionLocal()
        try:
            closed_trades = db.query(PaperTrade).filter(PaperTrade.status != "OPEN").all()
            total_pnl_usd = sum(t.pnl_amount for t in closed_trades if t.pnl_amount is not None)
            
            wins = sum(1 for t in closed_trades if t.outcome == "WIN")
            win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0
            
            current_balance = self.starting_capital + total_pnl_usd
            total_return_pct = (total_pnl_usd / self.starting_capital) * 100
            
            return {
                "total_pnl_usd": float(total_pnl_usd),
                "total_return_pct": round(total_return_pct, 2),
                "win_rate": round(win_rate, 2),
                "trade_count": len(closed_trades),
                "current_balance": round(current_balance, 2)
            }
        except Exception as e:
            logger.error(f"[PaperTrade] Error calculating performance: {e}")
            return {
                "total_pnl_usd": 0.0,
                "total_return_pct": 0.0,
                "win_rate": 0.0,
                "trade_count": 0,
                "current_balance": self.starting_capital
            }
        finally:
            db.close()

    def close_all_open(self, reason: str = "auto_exit") -> dict:
        """Force close all active open paper positions (e.g. at EOD 20:59 UTC)."""
        portfolio = self.get_portfolio()
        closed_count = 0
        try:
            for t in portfolio:
                res = self.close_trade(t["id"], t["current"], reason=reason)
                if res.get("success"):
                    closed_count += 1
            logger.info(f"[PaperTrade] EOD Auto-Exit complete. Closed {closed_count} open positions.")
            return {"success": True, "closed_count": closed_count}
        except Exception as e:
            logger.error(f"[PaperTrade] EOD Auto-Exit failed: {e}")
            return {"success": False, "error": str(e)}

    def check_stops(self):
        """Active risk checker to scan open trades and trigger SL/TP hits."""
        self.check_count += 1
        portfolio = self.get_portfolio()
        for t in portfolio:
            current = t["current"]
            sl = t["stop_loss"]
            tp = t["take_profit"]
            
            trigger = False
            reason = "manual"
            
            if t["side"] == "BUY":
                if current <= sl:
                    trigger = True
                    reason = "stopped_out"
                elif current >= tp:
                    trigger = True
                    reason = "take_profit"
            else:  # SHORT
                if current >= sl:
                    trigger = True
                    reason = "stopped_out"
                elif current <= tp:
                    trigger = True
                    reason = "take_profit"
                    
            if trigger:
                logger.warning(f"[PaperTrade] Risk target hit for #{t['id']} {t['symbol']} @ {current} | Trigger: {reason.upper()}")
                self.close_trade(t["id"], current, reason=reason)

paper_trading_service = PaperTradingService()
