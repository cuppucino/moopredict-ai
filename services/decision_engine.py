import json
from loguru import logger
from datetime import datetime, timedelta
from core.database import SessionLocal, DecisionLog, TradingKnowledge, PatternDB, PaperTrade, NewsIntel, SocialPost, MCPTResult
from services.ai_service import ai_service
from services.paper_trading import paper_trading_service
from services.moomoo_service import moomoo_service
from services.notifications import notification_queue

class DecisionEngine:
    def __init__(self):
        pass

    def run_decision_cycle(self):
        logger.info("[DecisionEngine] Starting autonomous decision cycle...")
        try:
            context = self._gather_context()
            memory = self._load_memory()
            prompt = self._build_prompt(context, memory)
            
            raw_decision = ai_service.query_decision(prompt)
            if "error" in raw_decision:
                logger.error(f"[DecisionEngine] AI failed: {raw_decision['error']}")
                self._log_decision({"action": "SKIP", "error": raw_decision["error"]})
                return
            
            decision = self._parse_decision(raw_decision)
            if "error" in decision:
                logger.error(f"[DecisionEngine] Parse failed: {decision['error']}")
                self._log_decision({"action": "SKIP", "error": decision["error"]})
                return
                
            self._route_action(decision, context)
        except Exception as e:
            logger.error(f"[DecisionEngine] Cycle failed: {e}")
            self._log_decision({"action": "SKIP", "error": str(e)})

    def _gather_context(self):
        portfolio = paper_trading_service.get_portfolio()
        perf = paper_trading_service.get_performance()
        balance = perf.get("current_balance", 257.64)

        cutoff = datetime.utcnow() - timedelta(hours=2)
        db = SessionLocal()
        try:
            news = db.query(NewsIntel).filter(NewsIntel.scraped_at >= cutoff).order_by(NewsIntel.scraped_at.desc()).limit(20).all()
            tweets = db.query(SocialPost).filter(SocialPost.platform == "X", SocialPost.scraped_at >= cutoff).order_by(SocialPost.scraped_at.desc()).limit(20).all()
            news_headlines = [n.headline for n in news]
            tweet_content = [t.content for t in tweets]
        finally:
            db.close()

        return {
            "portfolio": portfolio,
            "balance": balance,
            "open_positions_count": len(portfolio),
            "news": news_headlines,
            "twitter": tweet_content,
            "timestamp": datetime.utcnow().isoformat()
        }

    def _load_memory(self):
        db = SessionLocal()
        try:
            rules = db.query(TradingKnowledge).filter(TradingKnowledge.active == True).order_by(TradingKnowledge.importance.desc()).limit(15).all()
            lessons = db.query(PatternDB).filter(PatternDB.confirmed == True).order_by(PatternDB.created_at.desc()).limit(10).all()
            return {
                "rules": [r.rule for r in rules],
                "lessons": [l.lesson for l in lessons]
            }
        finally:
            db.close()

    def _build_prompt(self, context, memory):
        prompt = f"""
You are MooPredict AI, an autonomous paper day trading system managing ${context['balance']:.2f} in capital.
Current time (UTC): {context['timestamp']}
Open positions: {context['open_positions_count']}/3 maximum

RECENT NEWS (last 2 hours):
{json.dumps(context.get('news', []), indent=2)}

RECENT TWITTER/X (last 2 hours):
{json.dumps(context.get('twitter', []), indent=2)}

CURRENT PORTFOLIO:
{json.dumps(context.get('portfolio', []), indent=2)}

TRADING RULES (follow strictly):
{json.dumps(memory.get('rules', []), indent=2)}

LESSONS FROM PAST TRADES (avoid these mistakes):
{json.dumps(memory.get('lessons', []), indent=2)}

Based on the above, decide ONE action. If no strong catalyst exists, choose SKIP.
Respond ONLY with this JSON:
{{
  "action": "OPEN|CLOSE|HOLD|SKIP",
  "symbol": "TICKER or null",
  "side": "BUY|SHORT|null",
  "confidence": 0-100,
  "catalyst": "one sentence describing why",
  "reasoning": "detailed reasoning for this decision",
  "position_size_pct": 20-50,
  "close_trade_id": null
}}
"""
        return prompt

    def _parse_decision(self, raw_json):
        if not isinstance(raw_json, dict):
            return {"error": "Response is not JSON dict"}
        required = ["action", "confidence", "reasoning"]
        for r in required:
            if r not in raw_json:
                return {"error": f"Missing field: {r}"}
        return raw_json

    def _route_action(self, decision, context):
        action = decision.get("action", "SKIP")
        if action == "OPEN":
            self._execute_open(decision, context)
        elif action == "CLOSE":
            self._execute_close(decision)
        else:
            self._log_decision(decision)

        symbol = decision.get("symbol")
        reason = decision.get("catalyst") or decision.get("reasoning", "")
        reason_short = reason[:120] + "..." if len(reason) > 120 else reason
        action_emoji = {"OPEN": "📈", "CLOSE": "📉", "HOLD": "⏸", "SKIP": "⏭"}.get(action, "🔄")
        symbol_str = f" {symbol}" if symbol else ""
        msg = f"🔄 *Cycle complete:* {action_emoji} {action}{symbol_str}\n_{reason_short}_"
        notification_queue.enqueue(msg, level="info", category="general")

    def _execute_open(self, decision, context):
        if context["open_positions_count"] >= 3:
            logger.warning("[DecisionEngine] Max 3 positions reached. Skipping OPEN.")
            decision["error"] = "Max positions reached"
            decision["action"] = "SKIP"
            self._log_decision(decision)
            return

        symbol = decision.get("symbol")
        side = decision.get("side")
        size_pct = decision.get("position_size_pct", 20)
        
        # Check MCPT Gate
        db = SessionLocal()
        try:
            latest_gate = db.query(MCPTResult).filter(
                MCPTResult.ticker == symbol
            ).order_by(MCPTResult.run_at.desc()).first()
            
            if latest_gate:
                status = latest_gate.status
                logger.info(f"[DecisionEngine] MCPT Gate check for {symbol}: status={status}")
                
                if status == "DISABLED":
                    logger.warning(f"[DecisionEngine] Trade BLOCKED by MCPT Gate (status=DISABLED) for {symbol}.")
                    decision["error"] = f"Blocked by MCPT Gate (DISABLED)"
                    decision["action"] = "SKIP"
                    self._log_decision(decision)
                    return
                elif status == "WATCHLIST":
                    original_size = size_pct
                    size_pct = size_pct * 0.5
                    logger.warning(f"[DecisionEngine] Sizing PENALTY applied by MCPT Gate (status=WATCHLIST) for {symbol}: {original_size}% -> {size_pct}%")
        except Exception as error:
            logger.error(f"[DecisionEngine] Error querying MCPT Gate: {error}")
        finally:
            db.close()
        
        quote = moomoo_service.get_stock_quote(symbol)
        price = quote.get("last_price", 0) if quote else 0
        if price <= 0:
            decision["error"] = f"Invalid price for {symbol}"
            decision["action"] = "SKIP"
            self._log_decision(decision)
            return

        capital_to_use = context["balance"] * (size_pct / 100.0)
        qty = capital_to_use / price
        if qty < 1:
            decision["error"] = "Not enough capital for 1 share"
            decision["action"] = "SKIP"
            self._log_decision(decision)
            return

        log_id = self._log_decision(decision)

        res = paper_trading_service.open_trade(
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            strategy="AUTO",
            reasoning=decision.get("reasoning"),
            catalyst=decision.get("catalyst"),
            decision_id=log_id,
            news_context=context.get("news")
        )
        if not res.get("success"):
            logger.error(f"[DecisionEngine] Trade open failed: {res.get('error')}")

    def _execute_close(self, decision):
        trade_id = decision.get("close_trade_id")
        if not trade_id:
            return

        db = SessionLocal()
        try:
            trade = db.query(PaperTrade).filter(PaperTrade.id == trade_id).first()
            if not trade:
                return
            quote = moomoo_service.get_stock_quote(trade.symbol)
            price = quote.get("last_price", trade.entry_price) if quote else trade.entry_price
        finally:
            db.close()

        self._log_decision(decision)
        paper_trading_service.close_trade(trade_id, price, reason="AUTO_CLOSE")

    def _log_decision(self, decision):
        db = SessionLocal()
        try:
            log = DecisionLog(
                action=decision.get("action"),
                symbol=decision.get("symbol"),
                side=decision.get("side"),
                confidence=decision.get("confidence"),
                catalyst=decision.get("catalyst"),
                reasoning=decision.get("reasoning"),
                position_size_pct=decision.get("position_size_pct"),
                error=decision.get("error")
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            return log.id
        except Exception as e:
            logger.error(f"[DecisionEngine] Failed to log decision: {e}")
            db.rollback()
            return None
        finally:
            db.close()

decision_engine = DecisionEngine()
