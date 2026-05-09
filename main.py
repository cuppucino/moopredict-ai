import os
import time
import pytz
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from loguru import logger

# Configure logging to both console and file
LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "combined.log")
logger.add(LOG_FILE, rotation="500 MB", retention="10 days", level="INFO")

from core.database import init_db, get_db, UserWatchlist, NewsIntel, SocialPost
from core.scheduler import scheduler
from services.moomoo_service import moomoo_service
from services.notifications import notification_queue
from services.technical_analysis import ta_service
from services.earnings_calendar import earnings_service
from services.sector_analysis import sector_service
from services.daily_briefing import briefing_service
from services.options_flow import options_service
from services.trailing_stop_service import trailing_stop_service
from services.treasurer_service import treasurer_service
from services.strategy_service import strategy_service
from services.market_research import research_service
from services.trade_journal import trade_journal
from services.pattern_service import pattern_service
from services.alert_service import alert_service
from services.risk_service import risk_service
from services.prediction_service import prediction_service
from services.outlook_service import outlook_service
from services.validation_service import validation_service
from services.sentiment_service import sentiment_service
from services.pattern_stats import pattern_stats_service
from market_movers_endpoint import get_market_movers

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("🚀 Starting MooPredict Python Backend...")
    try:
        init_db()
        moomoo_service.connect()
        scheduler.init()
        logger.info("✅ Startup sequence complete.")
    except Exception as e:
        logger.error(f"❌ Startup failure: {e}")
    
    yield
    
    # Shutdown logic
    logger.info("Stopping MooPredict...")
    scheduler.shutdown()
    moomoo_service.close()

app = FastAPI(title="MooPredict AI API", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routes ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"ok": True, "uptime": time.time() - startup_time if 'startup_time' in globals() else 0}

@app.get("/api/notifications/pending")
def get_pending_notifications():
    return notification_queue.get_pending()

@app.post("/api/notifications/mark-sent")
def mark_notification_sent(payload: dict):
    notification_id = payload.get("id")
    if notification_id:
        notification_queue.mark_as_sent(notification_id)
    return {"success": True}

@app.get("/api/watchlist")
def get_watchlist(db: Session = Depends(get_db)):
    symbols = db.query(UserWatchlist).all()
    return [s.symbol for s in symbols]

@app.get("/api/positions")
@app.get("/api/moomoo/positions")
def get_positions():
    """Direct API access to current positions."""
    try:
        return moomoo_service.get_positions()
    except Exception as e:
        logger.error(f"API Error (positions): {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch positions")

@app.get("/api/balance")
@app.get("/api/moomoo/balance")
def get_balance():
    """Direct API access to account balance."""
    try:
        return moomoo_service.get_balance()
    except Exception as e:
        logger.error(f"API Error (balance): {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch balance")

@app.post("/api/watchlist")
def add_to_watchlist(payload: dict, db: Session = Depends(get_db)):
    symbol = payload.get("symbol", "").upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol required")
    
    existing = db.query(UserWatchlist).filter(UserWatchlist.symbol == symbol).first()
    if not existing:
        new_entry = UserWatchlist(symbol=symbol)
        db.add(new_entry)
        db.commit()
    return {"success": True, "symbol": symbol}

@app.delete("/api/watchlist/{symbol}")
def remove_from_watchlist(symbol: str, db: Session = Depends(get_db)):
    symbol = symbol.upper()
    db.query(UserWatchlist).filter(UserWatchlist.symbol == symbol).delete()
    db.commit()
    return {"success": True, "symbol": symbol}
    
@app.get("/api/news")
def get_news(limit: int = 20, db: Session = Depends(get_db)):
    """Fetch recent news articles."""
    articles = db.query(NewsIntel).order_by(NewsIntel.scraped_at.desc()).limit(limit).all()
    return [
        {
            "id": a.id,
            "headline": a.headline,
            "summary": a.summary,
            "source": a.source,
            "url": a.url,
            "scraped_at": a.scraped_at
        } for a in articles
    ]

@app.get("/api/social")
def get_social(limit: int = 20, db: Session = Depends(get_db)):
    """Fetch recent social media posts."""
    posts = db.query(SocialPost).order_by(SocialPost.scraped_at.desc()).limit(limit).all()
    return [
        {
            "id": p.id,
            "platform": p.platform,
            "author": p.author,
            "content": p.content,
            "url": p.post_url,
            "posted_at": p.posted_at,
            "scraped_at": p.scraped_at
        } for p in posts
    ]

@app.get("/api/ta/{symbol}")
def get_technical_analysis(symbol: str):
    """Fetch full technical analysis for a stock."""
    return ta_service.get_full_analysis(symbol)

@app.get("/api/earnings/{symbol}")
def get_earnings(symbol: str):
    """Fetch earnings info for a stock."""
    return earnings_service.get_stock_earnings(symbol)

@app.get("/api/earnings/watchlist")
def get_watchlist_earnings(db: Session = Depends(get_db)):
    """Fetch earnings for all stocks in watchlist."""
    symbols = db.query(UserWatchlist).all()
    symbol_list = [s.symbol for s in symbols]
    return earnings_service.get_watchlist_earnings(symbol_list)

@app.get("/api/sectors")
def get_sectors():
    """Fetch sector performance heatmap."""
    return sector_service.get_sector_performance()

@app.get("/api/sectors/{symbol}")
def get_sector_detail(symbol: str):
    """Fetch detailed performance for a specific sector ETF."""
    return sector_service.get_sector_detail(symbol)

@app.get("/api/briefing")
def get_briefing(quick: bool = False):
    """Fetch structured market and portfolio briefing."""
    return briefing_service.get_briefing_data(quick=quick)

@app.get("/api/market/movers")
async def market_movers(limit: int = 10):
    """Fetch top gainers, losers, and most active stocks."""
    return await get_market_movers(limit)

@app.get("/api/options/{symbol}")
def get_options_flow(symbol: str):
    """Fetch unusual options activity."""
    return options_service.get_unusual_activity(symbol)

@app.get("/api/implied/{symbol}")
def get_implied_move(symbol: str):
    """Fetch implied move from options straddle."""
    return options_service.get_implied_move(symbol)

@app.get("/api/validate/{symbol}")
def validate_symbol(symbol: str, direction: str = "UP"):
    """Perform a pre-prediction safety check."""
    return validation_service.validate_prediction(symbol, direction)

@app.get("/api/sentiment/{symbol}")
def get_sentiment(symbol: str):
    """Direct access to sentiment analysis."""
    return sentiment_service.get_sentiment_score(symbol)

@app.get("/api/research/{symbol}")
def get_market_research(symbol: str):
    """Generate a full AI deep-dive research report."""
    return {"report": research_service.perform_deep_dive(symbol)}

@app.get("/api/risk/size")
def get_position_size(symbol: str, price: float, risk: float = 2.0):
    """Calculate recommended position size."""
    return risk_service.calculate_position_size(symbol, price, risk)

def is_market_open():
    """Check if current time is within US market hours (13:30 - 21:00 UTC)."""
    now = datetime.now(pytz.utc)
    # Mon-Fri only
    if now.weekday() >= 5:
        return False
    
    start_time = now.replace(hour=13, minute=30, second=0, microsecond=0)
    end_time = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return start_time <= now <= end_time

@app.get("/api/scheduler/status")
def get_scheduler_status():
    """Diagnostic endpoint to check job health."""
    jobs = []
    for job in scheduler.scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger)
        })
    return {
        "is_running": scheduler.is_running,
        "jobs": jobs,
        "current_time_utc": datetime.now(pytz.utc).isoformat()
    }

@app.get("/api/journal")
def get_journal(limit: int = 20):
    """Fetch recent trade journal entries."""
    return trade_journal.get_recent(limit)

@app.get("/api/journal/stats")
def get_journal_stats():
    """Fetch trade statistics."""
    return trade_journal.get_stats()

@app.post("/api/journal/sync")
def sync_journal(payload: dict = None):
    """Sync trades from Moomoo."""
    days = payload.get("days", 30) if payload else 30
    return trade_journal.sync_past_trades(days)

@app.get("/api/patterns")
def get_patterns(limit: int = 20):
    """Fetch recorded market patterns."""
    return pattern_service.get_all(limit)

@app.get("/api/patterns/stats")
def get_pattern_stats():
    """Fetch accuracy stats for all patterns."""
    return pattern_stats_service.get_pattern_accuracy()

@app.post("/api/patterns")
def record_pattern(payload: dict):
    """Record a new market pattern observation."""
    name = payload.get("name")
    category = payload.get("category")
    obs = payload.get("observation")
    thesis = payload.get("thesis")
    symbols = payload.get("symbols", "")
    
    if not all([name, category, obs, thesis]):
        raise HTTPException(status_code=400, detail="name, category, observation, and thesis are required")
        
    pid = pattern_service.record_pattern(name, category, obs, thesis, symbols)
    return {"success": True, "pattern_id": pid}

@app.get("/api/alerts")
def get_alerts():
    """Fetch active price alerts."""
    return alert_service.get_active_alerts()

@app.post("/api/alerts")
def create_alert(payload: dict):
    """Create a new price alert."""
    symbol = payload.get("symbol")
    price = payload.get("price")
    direction = payload.get("direction", "ABOVE")
    action = payload.get("action", "notify")
    
    if not all([symbol, price, direction]):
        raise HTTPException(status_code=400, detail="symbol, price, and direction are required")
        
    aid = alert_service.add_alert(symbol, price, direction, action)
    return {"success": True, "alert_id": aid}

@app.post("/api/alerts/check")
def trigger_alert_check():
    """Manually trigger a price alert check."""
    alert_service.check_alerts()
    return {"success": True, "message": "Alert check triggered"}

@app.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: int):
    """Delete a price alert."""
    if alert_service.delete_alert(alert_id):
        return {"success": True, "message": f"Alert {alert_id} deleted"}
    raise HTTPException(status_code=404, detail="Alert not found")

@app.post("/api/notify")
def send_push_notification(payload: dict):
    """Send a push notification to Telegram via the queue."""
    message = payload.get("message")
    level = payload.get("level", "info")
    category = payload.get("category", "general")
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    
    notification_queue.enqueue(message, level, category)
    return {"success": True, "message": f"Notification enqueued in {category}"}

# ─── Predictions ─────────────────────────────────────────────────────────────
@app.get("/api/predictions")
def get_predictions(active: bool = True):
    if active:
        return prediction_service.get_active()
    return []

@app.post("/api/predictions")
def create_prediction(payload: dict):
    symbol = payload.get("symbol")
    direction = payload.get("direction")
    confidence = payload.get("confidence")
    catalyst = payload.get("catalyst")
    if not all([symbol, direction, confidence, catalyst]):
        raise HTTPException(status_code=400, detail="Missing required fields")
    
    return prediction_service.create_prediction(
        symbol=symbol,
        direction=direction,
        confidence=float(confidence),
        catalyst=catalyst,
        category=payload.get("category", "general"),
        timeframe_days=int(payload.get("timeframe", 7))
    )

@app.get("/api/predictions/stats")
def get_prediction_stats():
    return prediction_service.get_stats()

@app.get("/api/predscore")
def get_predscore_alias():
    """Alias for /api/predictions/stats as requested by user."""
    return prediction_service.get_stats()

@app.post("/api/outlook/generate")
def trigger_outlook():
    return {"report": outlook_service.generate_weekly_outlook()}

@app.get("/api/outlook")
def get_outlook_alias():
    """Alias for generating outlook via GET as requested by user."""
    return {"report": outlook_service.generate_weekly_outlook()}

@app.post("/api/test/notifications")
def test_notifications(type: str = "open", category: str = "general"):
    """Manually trigger notifications for testing."""
    if type == "open":
        notification_queue.enqueue("🔔 *TEST: US Market is OPEN!* 📈", "info", category)
    elif type == "close":
        notification_queue.enqueue("🔔 *TEST: US Market is closing in 5 minutes!* 📉", "warning", category)
    return {"success": True, "message": f"Test {type} notification enqueued in {category}"}

@app.post("/api/trade")
def place_trade(payload: dict):
    """API endpoint to place a trade with safety checks."""
    symbol = payload.get("symbol", "").upper().strip()
    qty = payload.get("qty")
    side = payload.get("side", "").upper().strip()
    order_type = payload.get("order_type", "MARKET")
    price = payload.get("price", 0.0)
    
    if not all([symbol, qty, side]):
        raise HTTPException(status_code=400, detail="symbol, qty, and side are required")
        
    # 🔴 Rule: Check earnings before trading
    earnings = earnings_service.get_stock_earnings(symbol)
    if earnings.get("earnings_dates"):
        try:
            next_date = datetime.strptime(earnings["earnings_dates"][0], "%Y-%m-%d")
            days_to_earnings = (next_date - datetime.now()).days
            if days_to_earnings <= 3 and days_to_earnings >= 0:
                logger.warning(f"[Trade] EARNINGS BLOCK: {symbol} has earnings in {days_to_earnings} days.")
                raise HTTPException(status_code=400, detail=f"Earnings catalyst within {days_to_earnings} days. Rule: HOLD through earnings.")
        except Exception as e:
            logger.error(f"[Trade] Earnings date parse error: {e}")

    result = moomoo_service.place_order(
        symbol=symbol,
        qty=float(qty),
        side=side,
        order_type=order_type,
        price=float(price)
    )
    
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["error"])
        
    # Auto-log to journal
    trade_id = trade_journal.log_trade(
        symbol=symbol,
        side=side,
        qty=float(qty),
        price=result.get("data", {}).get("last_price", price) or float(price),
        order_type=order_type
    )
    
    # 🛡️ Rule: Auto stop-loss on buy
    if side == "BUY":
        current_price = result.get("data", {}).get("last_price", price) or float(price)
        stop_price = float(current_price) * 0.90 # -10%
        alert_id = alert_service.add_alert(symbol, stop_price, "BELOW", action="notify")
        logger.info(f"[Trade] Auto stop-loss set for {symbol} at {stop_price:.2f} (Alert #{alert_id})")

    return {"success": True, "trade_id": trade_id, "data": result.get("data")}

# ─── Webhook (Command Handler) ──────────────────────────────────────────────
@app.post("/webhook")
async def handle_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        text = (body.get("text") or body.get("content") or "").strip()
        parts = text.split()
        if not parts:
            return {"content": "Empty command."}
        
        # Strip ! or / and normalize to lowercase
        raw_command = parts[0].lower()
        command = raw_command.lstrip("!/")
        logger.info(f"[Webhook] Command: {command} (raw: {raw_command}) | Args: {parts[1:]}")

        if command in ["checkstatus", "positions", "pos"]:
            positions = moomoo_service.get_positions()
            balance = moomoo_service.get_balance()
            
            pos_lines = "\n".join([
                f"• {p['symbol']}: {p['qty']} shares @ ${p['avg_price']} | P&L: {p['pnl_percent']}"
                for p in positions
            ]) if positions else "No open positions."

            cash = f"${balance['available_cash']:,.2f}" if balance else "N/A"
            assets = f"${balance['total_assets']:,.2f}" if balance else "N/A"

            return {
                "content": (
                    f"📊 *Moomoo Status*\n"
                    f"──────────────────\n"
                    f"💵 Cash: {cash}\n"
                    f"📈 Total Assets: {assets}\n\n"
                    f"*Positions:*\n{pos_lines}"
                )
            }

        if command == "watchlist":
            symbols = db.query(UserWatchlist).all()
            list_str = "\n".join([f"• {s.symbol}" for s in symbols]) if symbols else "Watchlist is empty."
            return {"content": f"📋 *Watchlist*\n──────────────\n{list_str}"}

        if command == "add" and len(parts) > 1:
            symbol = parts[1].upper()
            existing = db.query(UserWatchlist).filter(UserWatchlist.symbol == symbol).first()
            if not existing:
                db.add(UserWatchlist(symbol=symbol))
                db.commit()
            return {"content": f"✅ Added *{symbol}* to watchlist."}

        if command in ["remove", "rm"] and len(parts) > 1:
            symbol = parts[1].upper()
            db.query(UserWatchlist).filter(UserWatchlist.symbol == symbol).delete()
            db.commit()
            return {"content": f"🗑️ Removed *{symbol}* from watchlist."}

        if command == "status":
            wl_count = db.query(UserWatchlist).count()
            status_text = (
                f"✅ *Server Online (Python)*\n"
                f"📋 Watchlist: {wl_count} symbols\n"
                f"🔗 Moomoo: {'connected' if moomoo_service.is_connected else 'not connected'}"
            )
            return {"content": status_text}

        if command == "news":
            from services.news_scraper import news_scraper
            count = news_scraper.run()
            return {"content": f"🚀 News scrape triggered! Found {count} new articles."}

        if command == "x":
            from services.x_scraper import x_scraper
            count = x_scraper.run()
            return {"content": f"🐦 X scrape triggered! Found {count} new posts."}

        if command == "reddit":
            from services.reddit_scraper import reddit_scraper
            count = reddit_scraper.run()
            return {"content": f"🤖 Reddit scrape triggered! Found {count} new posts."}

        if command == "ta" and len(parts) > 1:
            symbol = parts[1].upper()
            analysis = ta_service.get_full_analysis(symbol)
            if "error" in analysis:
                return {"content": f"❌ Error: {analysis['error']}"}
            
            ma = analysis['moving_averages']
            macd = analysis['macd']
            
            report = (
                f"📈 *Technical Analysis: {symbol}*\n"
                f"──────────────────\n"
                f"💵 Price: ${analysis['price']:.2f}\n"
                f"📊 RSI: {analysis['rsi']}\n"
                f"📉 MACD: {macd['macd']:.4f} (Trend: {macd['trend']})\n"
                f"📏 SMA20: ${ma['sma20']:.2f}\n"
                f"📏 SMA50: ${ma['sma50']:.2f}\n"
                f"🎯 Summary: *{analysis['summary']}*"
            )
            return {"content": report}

        if command == "rsi" and len(parts) > 1:
            symbol = parts[1].upper()
            analysis = ta_service.get_full_analysis(symbol)
            if "error" in analysis: return {"content": f"❌ Error: {analysis['error']}"}
            return {"content": f"📊 *{symbol} RSI:* {analysis['rsi']}"}

        if command == "macd" and len(parts) > 1:
            symbol = parts[1].upper()
            analysis = ta_service.get_full_analysis(symbol)
            if "error" in analysis: return {"content": f"❌ Error: {analysis['error']}"}
            macd = analysis['macd']
            return {"content": f"📉 *{symbol} MACD:* {macd['macd']:.4f} ({macd['trend']})"}

        if command == "earnings" and len(parts) > 1:
            symbol = parts[1].upper()
            data = earnings_service.get_stock_earnings(symbol)
            date = data['earnings_dates'][0] if data['earnings_dates'] else "Unknown"
            history = ""
            if data.get("history"):
                h = data["history"][0]
                history = f"\nSurprise: {h['surprise_pct']}% (Last Q)"
            
            report = (
                f"📅 *{symbol} Earnings: {date}*\n"
                f"──────────────────\n"
                f"💰 Est EPS: ${data.get('eps_estimate', 'N/A')}\n"
                f"📉 Prev EPS: ${data.get('previous_eps', 'N/A')}\n"
                f"🎯 Rating: {data.get('analyst_rating', 'N/A')} (Mean)"
                f"{history}"
            )
            return {"content": report}

        if command == "earnings":
            symbols = db.query(UserWatchlist).all()
            if not symbols: return {"content": "Watchlist is empty."}
            results = earnings_service.get_watchlist_earnings([s.symbol for s in symbols])
            lines = []
            for r in results:
                if r.get("success"):
                    date = r['earnings_dates'][0] if r['earnings_dates'] else "N/A"
                    lines.append(f"• {r['symbol']}: {date}")
            return {"content": "📅 *Upcoming Earnings (Watchlist)*\n──────────────\n" + "\n".join(lines)}

        if command == "implied" and len(parts) > 1:
            symbol = parts[1].upper()
            data = options_service.get_implied_move(symbol)
            if not data.get("success"):
                return {"content": f"❌ Error: {data.get('error')}"}
            
            report = (
                f"🎲 *Implied Move: {symbol}*\n"
                f"──────────────────\n"
                f"📅 Expiry: {data['expiration']}\n"
                f"💵 Current: ${data['current_price']}\n"
                f"📏 Straddle: ${data['straddle_price']}\n"
                f"📈 Move: ±{data['implied_move_pct']}%\n"
                f"🎯 Range: ${data['range_lower']} - ${data['range_upper']}"
            )
            return {"content": report}

        if command == "sectors":
            results = sector_service.get_sector_performance()
            if not results: return {"content": "⚠️ Failed to fetch sector data."}
            lines = [f"• {s['symbol']} ({s['name']}): {s['change_1d']}%" for s in results[:10]]
            return {"content": "📊 *Sector Performance (1D)*\n──────────────\n" + "\n".join(lines)}

        if command == "briefing":
            briefing_service.generate_morning_briefing()
            return {"content": "🚀 Morning briefing triggered manually. Check Telegram in a moment!"}

        if command == "options" and len(parts) > 1:
            symbol = parts[1].upper()
            data = options_service.get_unusual_activity(symbol)
            if "error" in data: return {"content": f"❌ Error: {data['error']}"}
            
            lines = [f"• {o['strike']}{o['type']} {o['expiry']}: {o['ratio']}x Vol/OI" for o in data['unusual_activity'][:5]]
            report = (
                f"🎲 *Options Flow: {symbol}*\n"
                f"──────────────────\n"
                f"📊 Put/Call Ratio: {data['put_call_ratio']}\n"
                f"🎯 Sentiment: *{data['sentiment']}*\n\n"
                f"*Unusual Activity:*\n" + "\n".join(lines)
            )
            return {"content": report}

        if command == "pcr" and len(parts) > 1:
            symbol = parts[1].upper()
            data = options_service.get_pcr(symbol)
            if "error" in data: return {"content": f"❌ Error: {data['error']}"}
            return {"content": f"🎲 *{symbol} Put/Call Ratio:* {data['pcr']} ({data['sentiment']})"}

        if command == "risk":
            params = risk_service.get_current_risk_params()
            if "error" in params: return {"content": f"❌ Error: {params['error']}"}
            
            trading_status = "🟢 ENABLED" if not params["blocked"] else "🔴 BLOCKED"
            
            report = (
                f"🛡️ *Risk Dashboard*\n\n"
                f"📊 *Portfolio:*\n"
                f"• Value: ${params['total_assets']:,.2f}\n"
                f"• Heat: {params['heat_pct']:.1f}%\n"
                f"• At Risk: ${params['at_risk_usd']:,.2f} ({params['risk_pct']:.1f}%)\n\n"
                f"📈 *Risk Status:*\n"
                f"• Market: {params['market_status']}\n"
                f"• Drawdown: {params['drawdown_pct']:.1f}%\n"
                f"• Loss Streak: {params['streak']}\n"
                f"• Trading: {trading_status}\n\n"
                f"⚠️ *Warnings:*\n"
                f"{params['reason']}\n\n"
                f"💡 *Run /riskcheck to populate risk data for your watchlist.*"
            )
            return {"content": report}

        if command == "riskcheck":
            # Perform a manual risk analysis on the watchlist
            from services.technical_analysis import ta_service
            db = SessionLocal()
            try:
                symbols = db.query(UserWatchlist).all()
                if not symbols: return {"content": "Watchlist is empty. Add symbols with !add."}
                
                lines = []
                for s in symbols:
                    analysis = ta_service.get_full_analysis(s.symbol)
                    price = analysis.get("price", 0)
                    if price > 0:
                        size = risk_service.calculate_position_size(s.symbol, price)
                        if "error" in size:
                            lines.append(f"• {s.symbol}: ❌ {size['error']}")
                        else:
                            lines.append(f"• {s.symbol}: ${price} | {size['recommended_shares']} shares (Stop: ${size['stop_loss_price']})")
                    else:
                        lines.append(f"• {s.symbol}: ⚠️ Price unavailable")
                
                return {"content": "🛡️ *Watchlist Risk Check*\n──────────────\n" + "\n".join(lines)}
            finally:
                db.close()

        if command in ["research", "dd"] and len(parts) > 1:
            symbol = parts[1].upper()
            return {"content": research_service.perform_deep_dive(symbol)}

        if command in ["buy", "sell"] and len(parts) >= 3:
            # Usage: !buy AAPL 10 [limit_price]
            side = "BUY" if command == "buy" else "SELL"
            symbol = parts[1].upper()
            
            # 🔴 Rule: Market Hours Only (13:30 - 21:00 UTC)
            if not is_market_open():
                return {"content": "🔴 *MARKET CLOSED*\nTrades are only allowed during US market hours (13:30 - 21:00 UTC)."}

            try:
                qty = float(parts[2])
                price = float(parts[3]) if len(parts) > 3 else 0.0
                order_type = "LIMIT" if price > 0 else "MARKET"
                
                # 🔴 Rule: Check earnings before ANY trade (Buy or Sell)
                earnings = earnings_service.get_stock_earnings(symbol)
                if earnings.get("earnings_dates"):
                    try:
                        next_date = datetime.strptime(earnings["earnings_dates"][0], "%Y-%m-%d")
                        days_to_earnings = (next_date - datetime.now()).days
                        if days_to_earnings <= 3 and days_to_earnings >= 0:
                            return {"content": f"⚠️ *EARNINGS BLOCK*\n{symbol} has earnings in {days_to_earnings} days.\nRule: HOLD through earnings catalyst."}
                    except Exception as e:
                        logger.error(f"[Trade] Earnings date parse error: {e}")

                # 🔴 Rule: Risk Management Check (for Buy only)
                if side == "BUY":
                    # If price is 0, get it from quote
                    check_price = price if price > 0 else moomoo_service.get_stock_quote(symbol).get("last_price", 0)
                    risk_check = risk_service.calculate_position_size(symbol, check_price)
                    if "error" in risk_check:
                        return {"content": f"🛡️ *RISK BLOCK*\n{risk_check['error']}"}
                    
                    if qty > risk_check["recommended_shares"]:
                        return {"content": f"🛡️ *POSITION SIZE WARNING*\nRecommended max: {risk_check['recommended_shares']} shares.\nYou requested: {qty}.\nTo override, use `!buy {symbol} {qty} {price} force`."}

                # 🔴 Rule: Treasurer Approval Layer
                if "force" not in [p.lower() for p in parts]:
                    check_price = price if price > 0 else moomoo_service.get_stock_quote(symbol).get("last_price", 0)
                    approval_req = treasurer_service.request_approval(symbol, side, qty, check_price)
                    if not approval_req["auto_approved"]:
                        return {"content": (
                            f"🔒 *TREASURER APPROVAL REQUIRED*\n"
                            f"Trade: {side} {qty} {symbol} (${approval_req['total']:,.2f})\n"
                            f"Token: `{approval_req['token']}`\n"
                            f"Threshold: ${approval_req['threshold']:,.2f}\n"
                            f"Use `!approve {approval_req['token']}` to confirm."
                        )}

                result = moomoo_service.place_order(symbol, qty, side, order_type, price)
                if result["success"]:
                    # Auto-log to journal
                    last_price = result.get("data", {}).get("last_price", price) or price
                    trade_id = trade_journal.log_trade(
                        symbol=symbol,
                        side=side,
                        qty=qty,
                        price=last_price,
                        order_type=order_type
                    )
                    
                    # 🛡️ Rule: Auto trailing-stop on buy
                    alert_msg = ""
                    if side == "BUY":
                        sid = trailing_stop_service.create_trailing_stop(symbol, last_price, trade_id)
                        alert_msg = f"\n🛡️ Trailing stop set (Stop #{sid})"

                    return {"content": f"✅ *Trade Success*\nOrdered {qty} {symbol} ({side})\nJournaled as Trade #{trade_id}{alert_msg}"}
                else:
                    return {"content": f"❌ *Trade Failed*\n{result['error']}"}
            except ValueError:
                return {"content": "⚠️ Invalid quantity or price."}

        if command == "tradelog":
            mode = parts[1] if len(parts) > 1 else ""
            if mode == "stats":
                stats = trade_journal.get_stats()
                report = (
                    f"📊 *Trade Statistics*\n"
                    f"──────────────────\n"
                    f"📈 Total Trades: {stats['total']}\n"
                    f"🎯 Win Rate: {stats['win_rate']}\n"
                    f"💰 Total P&L: {stats['total_pnl']}\n"
                    f"📏 Avg %: {stats['avg_pnl_pct']}"
                )
                return {"content": report}
            
            recent = trade_journal.get_recent(5)
            lines = [f"• #{t['id']} {t['side']} {t['symbol']}: {t['status']} ({t['pnl']})" for t in recent]
            return {"content": "📝 *Recent Trades*\n──────────────\n" + ("\n".join(lines) if lines else "No trades logged yet.")}

        if command == "thesis" and len(parts) > 2:
            try:
                trade_id = int(parts[1])
                thesis = " ".join(parts[2:])
                if trade_journal.update_thesis(trade_id, thesis):
                    return {"content": f"✅ Thesis updated for Trade #{trade_id}."}
                return {"content": "❌ Trade not found."}
            except ValueError:
                return {"content": "⚠️ Invalid Trade ID."}

        if command == "lesson" and len(parts) > 2:
            try:
                trade_id = int(parts[1])
                lesson = " ".join(parts[2:])
                if trade_journal.add_lesson(trade_id, lesson):
                    return {"content": f"✅ Lesson added to Trade #{trade_id}."}
                return {"content": "❌ Trade not found."}
            except ValueError:
                return {"content": "⚠️ Invalid Trade ID."}

        if command == "close" and len(parts) > 2:
            try:
                trade_id = int(parts[1])
                exit_price = float(parts[2])
                lessons = " ".join(parts[3:]) if len(parts) > 3 else ""
                if trade_journal.close_trade(trade_id, exit_price, lessons):
                    return {"content": f"🏁 Trade #{trade_id} closed at ${exit_price}."}
                return {"content": "❌ Failed to close trade."}
            except ValueError:
                return {"content": "⚠️ Invalid ID or price."}

        if command == "pattern" and len(parts) > 4:
            # Usage: !pattern "Name" CATEGORY "Obs" "Thesis" [Symbols]
            # For simplicity, we'll use quotes for multi-word args or just take the rest
            try:
                # Basic parsing for demo purposes
                name = parts[1]
                category = parts[2]
                observation = parts[3]
                thesis = parts[4]
                symbols = parts[5] if len(parts) > 5 else ""
                pid = pattern_service.record_pattern(name, category, observation, thesis, symbols)
                return {"content": f"🧠 Pattern #{pid} recorded: {name}"}
            except:
                return {"content": "⚠️ Usage: !pattern NAME CATEGORY OBS THESIS [SYMBOLS]"}

        if command == "patterns":
            mode = parts[1] if len(parts) > 1 else ""
            if mode == "stats":
                stats = pattern_stats_service.get_pattern_accuracy()
                lines = [f"• {s['name']}: {s['accuracy_pct']}% ({s['wins']}/{s['total_predictions']})" for s in stats if s['total_predictions'] > 0]
                return {"content": "🧠 *Pattern Accuracy*\n──────────────\n" + ("\n".join(lines) if lines else "No resolved pattern data yet.")}
            
            patterns = pattern_service.get_all(5)
            lines = [f"• #{p['id']} {p['name']} ({p['category']})" for p in patterns]
            return {"content": "🧠 *Market Patterns*\n──────────────\n" + ("\n".join(lines) if lines else "No patterns recorded.")}

        if command == "size" and len(parts) > 1:
            symbol = parts[1].upper()
            # Try to get price from technical analysis
            analysis = ta_service.get_full_analysis(symbol)
            price = analysis.get("price")
            if not price or "error" in analysis:
                return {"content": f"❌ Could not fetch price for {symbol}."}
            
            risk_pct = float(parts[2]) if len(parts) > 2 else 10.0
            size = risk_service.calculate_position_size(symbol, price, risk_pct)
            
            if "error" in size:
                return {"content": f"❌ Risk Calculation Error: {size['error']}"}
                
            report = (
                f"📏 *Position Sizing: {symbol}*\n"
                f"──────────────────\n"
                f"💵 Price: ${price:.2f}\n"
                f"🛡️ Stop-Loss: ${size['stop_loss_price']:.2f} (-10%)\n"
                f"🎯 Recommended: *{size['recommended_shares']} shares*\n"
                f"💰 Total Cost: ${size['total_cost']:,.2f}\n"
                f"📊 Allocation: {size['allocation_percent']}% of portfolio"
            )
            return {"content": report}

        if command == "strategy":
            stats = strategy_service.get_strategy_stats()
            lines = [f"• {s['name']}: {s['win_rate']}% WR ({s['total']} trades, {s['avg_pnl']}% avg)" for s in stats]
            return {"content": "🧬 *Strategy Performance*\n──────────────\n" + ("\n".join(lines) if lines else "No strategy data yet.")}

        if command == "tag" and len(parts) > 2:
            try:
                trade_id = int(parts[1])
                strat = parts[2].upper()
                if strategy_service.tag_trade(trade_id, strat):
                    return {"content": f"✅ Trade #{trade_id} tagged as {strat}."}
                return {"content": f"❌ Trade not found or invalid strategy. Use: {', '.join(strategy_service.strategies)}"}
            except:
                return {"content": "⚠️ Usage: !tag TRADE_ID STRATEGY"}

        if command == "approve" and len(parts) > 1:
            token = parts[1].upper()
            approval = treasurer_service.get_approval(token)
            if not approval:
                return {"content": "❌ Invalid or expired token."}
            
            # Execute the trade
            symbol = approval["symbol"]
            qty = approval["qty"]
            side = approval["side"]
            price = approval["price"]
            order_type = "LIMIT" if price > 0 else "MARKET"
            
            result = moomoo_service.place_order(symbol, qty, side, order_type, price)
            if result["success"]:
                treasurer_service.consume_approval(token)
                trade_id = trade_journal.log_trade(symbol, side, qty, price, order_type)
                
                alert_msg = ""
                if side == "BUY":
                    sid = trailing_stop_service.create_trailing_stop(symbol, price, trade_id)
                    alert_msg = f"\n🛡️ Trailing stop set (Stop #{sid})"
                
                return {"content": f"✅ *Trade Approved & Executed*\n{side} {qty} {symbol} @ ${price}\nJournaled as Trade #{trade_id}{alert_msg}"}
            else:
                return {"content": f"❌ Trade Failed: {result['error']}"}

        if command == "reject" and len(parts) > 1:
            token = parts[1].upper()
            treasurer_service.consume_approval(token)
            return {"content": f"🗑️ Trade token {token} rejected."}

        if command == "stops":
            stops = trailing_stop_service.get_active()
            lines = [f"• #{s['id']} {s['symbol']}: ${s['stop_price']} (Trail {s['trail']}%, Highest ${s['highest']})" for s in stops]
            return {"content": "🛡️ *Active Trailing Stops*\n──────────────\n" + ("\n".join(lines) if lines else "No active trailing stops.")}

        if command == "rmstop" and len(parts) > 1:
            try:
                sid = int(parts[1])
                if trailing_stop_service.delete_stop(sid):
                    return {"content": f"🗑️ Trailing Stop #{sid} cancelled."}
                return {"content": f"❌ Stop #{sid} not found."}
            except:
                return {"content": "⚠️ Usage: !rmstop STOP_ID"}

        if command == "alert" and len(parts) > 3:
            # Usage: !alert SYMBOL DIRECTION PRICE
            symbol = parts[1].upper()
            direction = parts[2].upper()
            try:
                price = float(parts[3])
                aid = alert_service.add_alert(symbol, price, direction)
                return {"content": f"🔔 Alert #{aid} set for {symbol} {direction} {price}"}
            except:
                return {"content": "⚠️ Usage: !alert SYMBOL ABOVE|BELOW PRICE"}

        if command == "rmalert" and len(parts) > 1:
            try:
                aid = int(parts[1])
                if alert_service.delete_alert(aid):
                    return {"content": f"🗑️ Alert #{aid} deleted."}
                return {"content": f"❌ Alert #{aid} not found."}
            except:
                return {"content": "⚠️ Usage: !rmalert ALERT_ID"}

        if command == "predict" and len(parts) >= 5:
            # Usage: !predict SYMBOL DIRECTION CONFIDENCE "CATALYST" [DAYS]
            symbol = parts[1].upper()
            direction = parts[2].upper()
            try:
                confidence = float(parts[3])
                # Find catalyst in quotes if exists, or just take the rest
                text_blob = " ".join(parts[4:])
                import re
                quotes = re.findall(r'"(.*?)"', text_blob)
                # Check for 'force' at the end of the command
                force = "force" in [p.lower() for p in parts]
                
                # Check for pattern_id (e.g. pid:5)
                pattern_id = None
                for p in parts:
                    if p.startswith("pid:"):
                        try:
                            pattern_id = int(p.split(":")[1])
                        except:
                            pass
                catalyst = quotes[0] if quotes else text_blob
                
                res = prediction_service.create_prediction(
                    symbol=symbol, 
                    direction=direction, 
                    confidence=confidence, 
                    catalyst=catalyst,
                    pattern_id=pattern_id,
                    force=force
                )
                if res["success"]:
                    warn_text = "\n\n⚠️ *Warnings:*\n" + "\n".join(res["warnings"]) if res.get("warnings") else ""
                    return {"content": f"🔮 *Prediction #{res['prediction_id']} Recorded*\n{symbol} {direction} @ ${res['entry_price']:.2f}\nConfidence: {confidence}%\nCatalyst: {catalyst}{warn_text}"}
                
                if res.get("can_override"):
                    warn_text = "\n".join(res["warnings"])
                    return {"content": f"🔴 *Safety Check Failed*\n\n{warn_text}\n\nTo override, use `!predict {symbol} {direction} {confidence} \"{catalyst}\" force`"}
                
                return {"content": f"❌ Error: {res['error']}"}
            except Exception as e:
                logger.error(f"Prediction command error: {e}")
                return {"content": "⚠️ Usage: !predict SYMBOL UP|DOWN|FLAT CONF \"CATALYST\""}

        if command == "validate" and len(parts) > 1:
            symbol = parts[1].upper()
            direction = parts[2].upper() if len(parts) > 2 else "UP"
            res = validation_service.validate_prediction(symbol, direction)
            
            status = "✅ CLEAN" if not res["warnings"] else "⚠️ CAUTION" if res["passed"] else "🔴 REJECTED"
            warn_lines = "\n".join(res["warnings"]) if res["warnings"] else "No warnings found."
            
            report = (
                f"🛡️ *Safety Validation: {symbol}* ({direction})\n"
                f"──────────────────\n"
                f"Status: *{status}*\n"
                f"Recommendation: {res['recommendation']}\n\n"
                f"*Details:*\n{warn_lines}"
            )
            return {"content": report}

        if command == "outlook":
            report = outlook_service.generate_weekly_outlook()
            return {"content": "🚀 Weekly Outlook triggered! Check Telegram."}

        if command == "predscore":
            stats = prediction_service.get_stats()
            cats = "\n".join([f"• {k}: {v}%" for k, v in stats.get('by_category', {}).items()])
            return {
                "content": (
                    f"🎯 *Prediction Scorecard*\n"
                    f"──────────────────\n"
                    f"📈 Total Resolved: {stats['total_resolved']}\n"
                    f"🎯 Win Rate: {stats['accuracy_pct']}%\n\n"
                    f"*By Category:*\n{cats}"
                )
            }

        if command == "help":
            return {
                "content": (
                    f"🛡️ **MooPredict Trading System**\n\n"
                    f"**Trading Rules:**\n"
                    f"• Risk: 2% per trade (1% if >$500)\n"
                    f"• Max drawdown: 10% (trading blocked)\n"
                    f"• Loss streak: 5+ (trading blocked)\n"
                    f"• Loss streak: 3+ (risk halved)\n"
                    f"• Market hours: 13:30-21:00 UTC\n"
                    f"• Earnings block: No trades if earnings < 3 days\n\n"
                    f"**Safety Features:**\n"
                    f"• Trailing stops (5% leveraged, 10% stocks)\n"
                    f"• Treasurer approval for large trades (>$50)\n\n"
                    f"**Commands:**\n"
                    f"• !risk — full risk dashboard\n"
                    f"• !riskcheck — analyze risk for watchlist\n"
                    f"• !buy SYMBOL QTY [PRICE] — place order\n"
                    f"• !sell SYMBOL QTY [PRICE] — place order\n"
                    f"• !checkstatus — positions & balance\n"
                    f"• !ta SYMBOL — full technical analysis\n"
                    f"• !tradelog [stats] — view history\n"
                    f"• !strategy — view evolution stats\n"
                    f"• !watchlist — show watchlist\n"
                    f"• !add SYMBOL — add to watchlist\n"
                    f"• !remove SYMBOL — remove from watchlist\n"
                    f"• !news — trigger news scrape\n"
                    f"• !status — server health"
                )
            }

        return {"content": "Unknown command. Try !help"}

    except Exception as e:
        logger.error(f"[Webhook] Error: {e}")
        return {"content": f"⚠️ Error: {str(e)}"}

# Define startup time at the top
startup_time = time.time()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3001)
