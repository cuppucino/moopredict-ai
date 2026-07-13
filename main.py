import os
import time
import pytz
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from loguru import logger

# Configure logging to both console and file
LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "combined.log")
logger.add(LOG_FILE, rotation="500 MB", retention="10 days", level="INFO")

from core.database import init_db, get_db, UserWatchlist, NewsIntel, SocialPost, SessionLocal, Prediction, MCPTResult
from core.scheduler import scheduler
from services.moomoo_service import moomoo_service
from services.notifications import notification_queue
from services.technical_analysis import ta_service
from services.earnings_calendar import earnings_service
from services.sector_analysis import sector_service
from services.daily_briefing import briefing_service
from services._legacy.options_flow import options_service
from services.trailing_stop_service import trailing_stop_service
from services._legacy.treasurer_service import treasurer_service
from services.strategy_service import strategy_service
from services._legacy.market_research import research_service
from services.trade_journal import trade_journal
from services._legacy.pattern_service import pattern_service
from services.alert_service import alert_service
from services.risk_service import risk_service
from services.prediction_service import prediction_service
from services._legacy.outlook_service import outlook_service
from services.validation_service import validation_service
from services.sentiment_service import sentiment_service
from services._legacy.pattern_stats import pattern_stats_service
from services.volume_flow_service import vol_flow_service
from services.heartbeat_monitor import heartbeat_monitor
from services.paper_trading import paper_trading_service

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("🚀 Starting MooPredict Python Backend...")
    try:
        init_db()
    except Exception as e:
        logger.error(f"❌ DB init failure: {e}")

    # Attempt moomoo connect with a STRICT timeout so a dead OpenD can never
    # block FastAPI startup again (see 2026-06-27 41h wedge incident).
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, moomoo_service.connect),
            timeout=10.0,
        )
        if moomoo_service.is_connected:
            logger.success("[Moomoo] Connected at startup.")
        else:
            logger.warning("[Moomoo] connect() returned without success. Will retry in background.")
    except asyncio.TimeoutError:
        logger.error("[Moomoo] Startup connect timed out after 10s — likely OpenD is down. Continuing without moomoo; yfinance fallback will serve prices. Background reconnect will retry every 5 min.")
    except Exception as e:
        logger.error(f"[Moomoo] Startup connect raised: {e}. Continuing without moomoo.")

    try:
        scheduler.init()
        logger.info("✅ Startup sequence complete (moomoo connected: %s)." % moomoo_service.is_connected)
    except Exception as e:
        logger.error(f"❌ Scheduler init failure: {e}")

    yield

    # Shutdown logic
    logger.info("Stopping MooPredict...")
    try:
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"Scheduler shutdown error: {e}")
    try:
        moomoo_service.close()
    except Exception as e:
        logger.error(f"Moomoo shutdown error: {e}")

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

@app.get("/api/health")
def system_health():
    """At-a-glance system status — moomoo, scrapers, scheduler, predictions."""
    from datetime import datetime
    from core.database import SessionLocal, Prediction
    from services.data_freshness import freshness_registry
    db = SessionLocal()
    try:
        pending = db.query(Prediction).filter(Prediction.outcome == None).count()
        total = db.query(Prediction).count()
    finally:
        db.close()
    return {
        "ok": True,
        "uptime_sec": int(time.time() - startup_time) if 'startup_time' in globals() else 0,
        "moomoo": {
            "connected": bool(moomoo_service.is_connected),
            "host": getattr(moomoo_service, "host", None),
        },
        "scheduler_running": bool(getattr(scheduler, "is_running", False)),
        "predictions": {
            "active": pending,
            "total": total,
        },
        "freshness": {
            "moomoo_quote_age_min": freshness_registry.get_age_minutes("moomoo_quote") if hasattr(freshness_registry, "get_age_minutes") else None,
        },
        "checked_at": datetime.utcnow().isoformat(),
    }

@app.get("/api/v1/commands/freshness")
def get_v1_freshness():
    from services.data_freshness import freshness_registry
    return freshness_registry.get_staleness_report()

@app.get("/api/v1/commands/status")
def get_v1_status(db: Session = Depends(get_db)):
    wl_count = db.query(UserWatchlist).count()
    uptime_sec = time.time() - startup_time if 'startup_time' in globals() else 0
    from services.data_freshness import freshness_registry
    report = freshness_registry.get_staleness_report()
    stale_count = len([a for a in report.values() if a > 120])
    
    return {
        "is_online": True,
        "uptime_hours": round(uptime_sec / 3600, 1),
        "watchlist_count": wl_count,
        "moomoo_connected": moomoo_service.is_connected,
        "stale_sources_count": stale_count,
        "timestamp_utc": datetime.utcnow().isoformat()
    }

@app.get("/api/v1/mcpt/strategies")
def get_mcpt_strategies(db: Session = Depends(get_db)):
    """
    Exposes the latest strategy status, statistical p-values, EMA, and
    warmup bootstrapping status for the frontend dashboard.
    """
    try:
        from sqlalchemy import func
        # Subquery to get the max run_at for each strategy/ticker pair
        subq = db.query(
            MCPTResult.strategy,
            MCPTResult.ticker,
            func.max(MCPTResult.run_at).label("max_run_at")
        ).group_by(MCPTResult.strategy, MCPTResult.ticker).subquery()
        
        # Join back to extract full latest record
        latest_results = db.query(MCPTResult).join(
            subq,
            (MCPTResult.strategy == subq.c.strategy) &
            (MCPTResult.ticker == subq.c.ticker) &
            (MCPTResult.run_at == subq.c.max_run_at)
        ).all()
        
        response_data = []
        for r in latest_results:
            # Dynamically count total runs to determine bootstrap progress
            total_runs = db.query(MCPTResult).filter(
                MCPTResult.strategy == r.strategy,
                MCPTResult.ticker == r.ticker
            ).count()
            
            response_data.append({
                "id": r.id,
                "strategy": r.strategy,
                "ticker": r.ticker,
                "run_at": r.run_at.isoformat() if r.run_at else None,
                "insample_p": r.insample_p,
                "wf_p": r.wf_p,
                "ema_p": r.ema_p,
                "real_pf": r.real_pf,
                "status": r.status,
                "bootstrap_pending": total_runs < 5,
                "total_runs": total_runs
            })
        return response_data
    except Exception as error:
        logger.error(f"Error fetching MCPT strategies: {error}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(error)}")

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

@app.get("/api/ta/score/{symbol}")
def get_ta_score(symbol: str):
    """Fetch composite TA score (0-100)."""
    from services.ta_engine import ta_engine
    return ta_engine.compute_all(symbol)

@app.get("/api/ta/regime/{symbol}")
def get_market_regime(symbol: str):
    """Fetch current market regime (TRENDING/RANGE)."""
    from services.ta_engine import ta_engine
    res = ta_engine.compute_all(symbol)
    return {"symbol": symbol, "regime": res.get("regime", "UNKNOWN")}

@app.get("/api/sentiment/breakdown/{symbol}")
def get_sentiment_breakdown(symbol: str):
    """Fetch detailed sentiment breakdown (News vs Social)."""
    from services.sentiment_engine import sentiment_engine
    return sentiment_engine.score_symbol(symbol)

@app.get("/api/options/analysis/{symbol}")
def get_options_analysis(symbol: str):
    """Fetch detailed options metrics (Max Pain, GEX)."""
    from services._legacy.options_engine import options_engine
    return options_engine.get_chain_data(symbol)

@app.get("/api/risk/var/{symbol}")
def get_var(symbol: str, confidence: float = 0.95, days: int = 1):
    """Value at Risk and Expected Shortfall."""
    from services.risk_engine import risk_engine
    return risk_engine.calculate_var(symbol, confidence, days)

@app.get("/api/risk/kelly")
def get_kelly(symbol: str = None):
    """Kelly Criterion optimal position sizing."""
    from services.risk_engine import risk_engine
    return risk_engine.calculate_kelly(symbol)

@app.get("/api/risk/sharpe/{symbol}")
def get_sharpe(symbol: str):
    """Annualized Sharpe Ratio."""
    from services.risk_engine import risk_engine
    return risk_engine.calculate_sharpe(symbol)

@app.get("/api/risk/smartsize/{symbol}")
def get_smart_size(symbol: str, price: float = None):
    """Kelly + VaR hybrid position sizing."""
    from services.risk_engine import risk_engine
    if not price:
        quote = moomoo_service.get_stock_quote(symbol)
        price = quote.get("last_price") or 0
    if price <= 0:
        return {"error": "Could not determine price."}
    return risk_engine.smart_position_size(symbol, price)

@app.get("/api/backtest/rsi/{symbol}")
def get_backtest_rsi(symbol: str, lookback: int = 365):
    """Backtest RSI strategy."""
    from services._legacy.backtest_engine import backtest_engine
    return backtest_engine.backtest_rsi(symbol, lookback_days=lookback)


@app.get("/api/backtest/ema/{symbol}")
def get_backtest_ema(symbol: str, lookback: int = 365):
    """Backtest EMA crossover strategy."""
    from services._legacy.backtest_engine import backtest_engine
    return backtest_engine.backtest_ema_crossover(symbol, lookback_days=lookback)

@app.get("/api/ml/forecast/{symbol}")
def get_ml_forecast(symbol: str):
    """LSTM Price Forecast."""
    from services._legacy.ml_engine import ml_engine
    return ml_engine.predict_price_lstm(symbol)

@app.get("/api/ml/trend/{symbol}")
def get_ml_trend(symbol: str):
    """Trend Prediction (Random Forest)."""
    from services._legacy.ml_engine import ml_engine
    return ml_engine.predict_trend_random_forest(symbol)

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

@app.get("/api/rrg")
def get_rrg():
    """Calculate Relative Rotation Graph (RRG) quadrants."""
    return sector_service.calculate_rrg()

@app.get("/api/sectors/{symbol}")
def get_sector_detail(symbol: str):
    """Fetch detailed performance for a specific sector ETF."""
    return sector_service.get_sector_detail(symbol)

@app.get("/api/briefing")
def get_briefing(quick: bool = False):
    """Fetch structured market and portfolio briefing."""
    return briefing_service.get_briefing_data(quick=quick)

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

@app.get("/api/darkpool/{symbol}")
def get_darkpool_data(symbol: str):
    """Fetch institutional dark pool prints."""
    from services._legacy.darkpool_service import darkpool_service
    return darkpool_service.get_summary(symbol)

@app.get("/api/uoa/{symbol}")
def get_uoa_flow(symbol: str):
    """Fetch Unusual Options Activity (UOA) alerts."""
    from services._legacy.uoa_service import uoa_service
    return uoa_service.get_summary(symbol)

@app.get("/api/sec/{symbol}")
def get_sec_filings(symbol: str):
    """Fetch insider trades and institutional filings."""
    from services._legacy.sec_filing_service import sec_service
    return sec_service.get_summary(symbol)

@app.get("/api/vol/{symbol}")
def get_volume_flow(symbol: str, res: str = "1w"):
    """Fetch Institutional Volume Flow analysis."""
    return vol_flow_service.calculate_volume_profile(symbol, res)

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

@app.get("/api/heartbeat/status")
def get_heartbeat_status():
    """Fetch current heartbeat state and last check results."""
    return heartbeat_monitor.state

@app.post("/api/heartbeat/check")
def trigger_heartbeat_check():
    """Manually trigger a full heartbeat check."""
    heartbeat_monitor.run_full_check()
    return {"success": True, "message": "Manual heartbeat check triggered"}

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
def get_predictions(active: bool = True, limit: int = 50, resolved_only: bool = False):
    if active:
        return prediction_service.get_active()
    return prediction_service.get_all(limit=limit, resolved_only=resolved_only)

@app.post("/api/predictions")
def create_prediction(payload: dict):
    symbol = payload.get("symbol")
    direction = payload.get("direction")
    confidence = payload.get("confidence")
    catalyst = payload.get("catalyst")
    if not all([symbol, direction, confidence, catalyst]):
        raise HTTPException(status_code=400, detail="Missing required fields")
    
    try:
        parsed_confidence = float(confidence)
        parsed_timeframe = int(payload.get("timeframe", 7))
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid type in prediction creation request: {e}")
        raise HTTPException(status_code=400, detail="Invalid confidence or timeframe parameter.")

    try:
        target_price = payload.get("target_price")
        if target_price is not None:
            try:
                target_price = float(target_price)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid target_price.")

        res = prediction_service.create_prediction(
            symbol=symbol,
            direction=direction,
            confidence=parsed_confidence,
            catalyst=catalyst,
            category=payload.get("category", "general"),
            timeframe_days=parsed_timeframe,
            target_price=target_price,
            force=bool(payload.get("force", False)),
            prediction_tag=payload.get("prediction_tag")
        )
    except Exception as e:
        logger.error(f"Failed to create prediction due to service error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error while creating prediction.")

    if not res.get("success"):
        if res.get("error") == "quote_timeout":
            raise HTTPException(status_code=504, detail="Timeout retrieving stock quote.")
        if res.get("error") == "Safety check failed.":
            return JSONResponse(
                status_code=422,
                content={
                    "success": False,
                    "error": "Safety check failed.",
                    "warnings": res.get("warnings", []),
                    "can_override": res.get("can_override", False),
                    "hint": "Pass 'force': true in the payload to override after reviewing warnings."
                }
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create prediction."))
    return res

@app.get("/api/morning_brief/today")
def get_morning_brief_today():
    """Return today's brief as JSON. Generates if missing."""
    from services.morning_brief_engine import morning_brief_engine
    from datetime import date
    return morning_brief_engine.generate_brief(date.today())

@app.get("/api/morning_brief/today/markdown", response_class=None)
def get_morning_brief_markdown():
    """Return today's brief as markdown text — copy-paste ready for openclaw."""
    from services.morning_brief_engine import morning_brief_engine
    from datetime import date
    from fastapi.responses import PlainTextResponse
    result = morning_brief_engine.generate_brief(date.today())
    md = morning_brief_engine._render_markdown(result)
    return PlainTextResponse(md)

@app.get("/api/morning_brief/{brief_date}")
def get_morning_brief_by_date(brief_date: str):
    """Return brief for a specific date (YYYY-MM-DD). Generates if missing."""
    from services.morning_brief_engine import morning_brief_engine
    from datetime import date
    try:
        d = date.fromisoformat(brief_date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {brief_date}. Use YYYY-MM-DD.")
    return morning_brief_engine.generate_brief(d)

@app.get("/api/track-record/analytics")
def get_track_record_analytics(window_days: int = 30):
    """
    Deep-cut analytics: WR at 4 threshold levels, breakdowns by leverage class + regime.
    Enables data-driven answers to:
      - Should threshold be 0.5% or 0.1%?
      - Do leveraged predictions have different WR than 1x?
      - Do regime-aligned predictions win more?
    """
    from datetime import timedelta
    from core.database import SessionLocal, Prediction
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=window_days)
        preds = db.query(Prediction).filter(
            Prediction.outcome.in_(["RIGHT", "WRONG"]),
            Prediction.created_at >= cutoff,
        ).all()
        n = len(preds)
        if n == 0:
            return {"n_resolved": 0, "window_days": window_days, "message": "no resolved predictions in window"}

        def wr(attr):
            hits = [1 for p in preds if getattr(p, attr) is True]
            return round(len(hits) / n, 4)

        # By leverage class
        by_leverage = {}
        for p in preds:
            cls = p.leverage_class or "unknown"
            if cls not in by_leverage:
                by_leverage[cls] = {"n": 0, "wins_05pct": 0, "wins_01pct": 0}
            by_leverage[cls]["n"] += 1
            if p.would_be_right_at_05pct is True:
                by_leverage[cls]["wins_05pct"] += 1
            if p.would_be_right_at_01pct is True:
                by_leverage[cls]["wins_01pct"] += 1
        for cls, d in by_leverage.items():
            d["wr_at_05pct"] = round(d["wins_05pct"] / d["n"], 4)
            d["wr_at_01pct"] = round(d["wins_01pct"] / d["n"], 4)

        # By regime
        by_regime = {}
        for p in preds:
            reg = p.regime_at_open or "not_tagged"
            if reg not in by_regime:
                by_regime[reg] = {"n": 0, "wins_05pct": 0, "wins_01pct": 0}
            by_regime[reg]["n"] += 1
            if p.would_be_right_at_05pct is True:
                by_regime[reg]["wins_05pct"] += 1
            if p.would_be_right_at_01pct is True:
                by_regime[reg]["wins_01pct"] += 1
        for reg, d in by_regime.items():
            d["wr_at_05pct"] = round(d["wins_05pct"] / d["n"], 4)
            d["wr_at_01pct"] = round(d["wins_01pct"] / d["n"], 4)

        return {
            "n_resolved": n,
            "window_days": window_days,
            "wr_by_threshold": {
                "at_0.1pct": wr("would_be_right_at_01pct"),
                "at_0.2pct": wr("would_be_right_at_02pct"),
                "at_0.3pct": wr("would_be_right_at_03pct"),
                "at_0.5pct": wr("would_be_right_at_05pct"),
            },
            "wr_by_leverage_class": by_leverage,
            "wr_by_regime": by_regime,
        }
    finally:
        db.close()


@app.post("/api/predictions/validate")
def validate_proposal(payload: dict):
    """
    Mechanical rule-checker for prediction proposals (the openclaw rulebook, encoded).
    Openclaw calls this BEFORE showing kf a proposal — kf's audit then covers judgment
    only (thesis quality, regime honesty), not arithmetic.

    Body: {"predictions": [{"symbol", "direction", "confidence", "entry", "target",
                             "stop", "timeframe"}, ...]}
    Returns per-prediction violations + batch-level warnings.
    """
    from services.prediction_service import classify_leverage
    from services.morning_brief_engine import get_cluster_for
    from core.database import SessionLocal, Prediction

    preds = payload.get("predictions", [])
    if not preds:
        raise HTTPException(status_code=400, detail="predictions list required")

    # Active predictions for cluster-conflict checks
    db = SessionLocal()
    try:
        active = db.query(Prediction).filter(Prediction.outcome.is_(None)).all()
        active_clusters = {get_cluster_for(p.symbol): p.symbol for p in active if get_cluster_for(p.symbol)}
        active_symbols = {p.symbol for p in active}
    finally:
        db.close()

    results, batch_clusters, up_count = [], {}, 0
    for i, p in enumerate(preds):
        v = []  # violations
        sym = (p.get("symbol") or "").upper().strip()
        direction = (p.get("direction") or "").upper().strip()
        conf = float(p.get("confidence") or 0)
        entry, target, stop = p.get("entry"), p.get("target"), p.get("stop")
        tf = p.get("timeframe")
        lev = classify_leverage(sym)
        cluster = get_cluster_for(sym)

        # Rule: leverage conviction gate
        if not lev.startswith("1x") and conf < 60:
            v.append(f"LEVERAGE GATE: {sym} is {lev} — requires >=60% conviction, got {conf:.0f}%")
        # Rule: confidence bounds
        if conf and not (45 <= conf <= 85):
            v.append(f"CONFIDENCE: {conf:.0f}% outside sane range 45-85")
        # Rule: timeframe 1-3
        if tf is not None and int(tf) not in (1, 2, 3):
            v.append(f"TIMEFRAME: {tf} not in 1-3")
        # Rules: stop/target direction + R:R (computed server-side — no self-math errors)
        rr = None
        if entry and target and stop and direction in ("UP", "DOWN"):
            entry, target, stop = float(entry), float(target), float(stop)
            if direction == "UP":
                if stop >= entry: v.append(f"STOP DIRECTION: LONG stop {stop} must be BELOW entry {entry}")
                if target <= entry: v.append(f"TARGET DIRECTION: LONG target {target} must be ABOVE entry {entry}")
                if stop < entry < target:
                    rr = round((target - entry) / (entry - stop), 2)
            else:
                if stop <= entry: v.append(f"STOP DIRECTION: SHORT stop {stop} must be ABOVE entry {entry}")
                if target >= entry: v.append(f"TARGET DIRECTION: SHORT target {target} must be BELOW entry {entry}")
                if target < entry < stop:
                    rr = round((entry - target) / (stop - entry), 2)
            if rr is not None and rr < 1.2:
                v.append(f"R:R FLOOR: {rr}:1 below 1.2:1 minimum")
        # Rule: cluster conflict vs active book
        if cluster and cluster in active_clusters:
            v.append(f"CLUSTER CONFLICT: {cluster} already occupied by active {active_clusters[cluster]}")
        if sym in active_symbols:
            v.append(f"DUPLICATE: {sym} already has an active prediction")
        # Rule: cluster conflict within this batch
        if cluster:
            if cluster in batch_clusters:
                v.append(f"BATCH CLUSTER CONFLICT: {cluster} already used by {batch_clusters[cluster]} in this proposal")
            else:
                batch_clusters[cluster] = sym
        if direction == "UP":
            up_count += 1
        results.append({"symbol": sym, "leverage_class": lev, "cluster": cluster,
                        "rr_computed": rr, "violations": v, "passed": not v})

    warnings = []
    if up_count > 3:
        warnings.append(f"CORRELATION: {up_count} UP bets in one batch — max 2-3 correlated longs per book (weekend 1/6 lesson)")

    return {"predictions": results,
            "batch_warnings": warnings,
            "all_passed": all(r["passed"] for r in results) and not warnings}


@app.post("/api/predictions/{prediction_id}/cancel")
def cancel_prediction(prediction_id: int, payload: dict = None):
    """
    Cancel a pending prediction. Marks outcome as CANCELLED so it's
    excluded from track-record metrics (only RIGHT/WRONG count).
    Use for duplicates, fat-fingers, or pre-audit posts being replaced.
    """
    from datetime import datetime
    from core.database import SessionLocal, Prediction
    db = SessionLocal()
    try:
        p = db.query(Prediction).filter(Prediction.id == prediction_id).first()
        if not p:
            raise HTTPException(status_code=404, detail=f"Prediction #{prediction_id} not found")
        if p.outcome is not None:
            raise HTTPException(status_code=400, detail=f"Prediction #{prediction_id} already resolved as {p.outcome}")
        reason = (payload or {}).get("reason", "manual cancel")
        p.outcome = "CANCELLED"
        p.exit_price = p.entry_price
        p.actual_move_pct = 0.0
        p.resolved_at = datetime.utcnow()
        p.notes = f"CANCELLED: {reason}"
        db.commit()
        return {"success": True, "id": p.id, "symbol": p.symbol, "outcome": "CANCELLED", "reason": reason}
    finally:
        db.close()

@app.get("/api/predictions/stats")
def get_prediction_stats():
    return prediction_service.get_stats()

@app.get("/api/track-record")
def get_track_record(db: Session = Depends(get_db)):
    """Fetch openclaw's paper-trading track record and real trading unlock state."""
    try:
        from services.track_record import compute_metrics
        from core.database import SystemState
        
        metrics = compute_metrics(db)
        state = db.query(SystemState).filter(SystemState.key == "real_trading_unlocked").first()
        
        return {
            **metrics,
            "real_trading_unlocked": bool(state and state.real_trading_unlocked),
            "unlock_history": (state.unlock_history if state else []),
        }
    except Exception as e:
        logger.error(f"Error fetching track record API: {e}")
        raise HTTPException(status_code=500, detail=f"Database or computation error: {str(e)}")

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
        # Support multiple common webhook field names
        text = (
            body.get("text") or 
            body.get("content") or 
            body.get("message") or 
            body.get("msg") or 
            body.get("data") or 
            ""
        ).strip()
        
        parts = text.split()
        if not parts:
            logger.warning(f"[Webhook] Received empty or unparseable payload: {body}")
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
            uptime = round((time.time() - startup_time) / 3600, 1)
            from services.data_freshness import freshness_registry
            report = freshness_registry.get_staleness_report()
            stale = len([a for a in report.values() if a > 120])
            
            status_text = (
                f"✅ *Server Online*\n"
                f"⏱️ Uptime: {uptime} hours\n"
                f"📋 Watchlist: {wl_count} symbols\n"
                f"🔗 Moomoo: {'connected' if moomoo_service.is_connected else '🔴 DISCONNECTED'}\n"
                f"🧊 Stale Sources: {stale}"
            )
            return {"content": status_text}

        if command == "freshness":
            from services.data_freshness import freshness_registry
            report = freshness_registry.get_staleness_report()
            lines = [f"• {s}: {age} min" for s, age in report.items()]
            return {"content": "🧊 *Data Freshness Report*\n──────────────\n" + ("\n".join(lines) if lines else "No data registered.")}

        if command == "reconnect":
            success = moomoo_service.auto_reconnect()
            return {"content": "🔄 *Moomoo Reconnect*\n──────────────\nStatus: " + ("✅ SUCCESS" if success else "❌ FAILED")}

        if command == "heartbeat":
            heartbeat_monitor.run_full_check()
            state = heartbeat_monitor.state
            last_check = state.get("last_check_utc", "Never")
            return {
                "content": (
                    f"💓 *Heartbeat Status*\n"
                    f"──────────────────\n"
                    f"🕒 Last Check: {last_check}\n"
                    f"📈 Positions Tracked: {len(state.get('positions', {}))}\n"
                    f"🌀 Sectors Tracked: {len(state.get('sectors', {}))}\n"
                    f"🖥️ OpenD: {'✅ OK' if state.get('health', {}).get('opend_connected') else '❌ Error'}\n\n"
                    f"🚀 Manual check triggered and finished."
                )
            }

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
            return {"content": f"📉 *{symbol} MACD:* {macd['macd']:.4f} ({macd['trend']})\n_Note: MACD is ranked F-Tier (Avoid)._"}

        if command == "vwap" and len(parts) > 1:
            symbol = parts[1].upper()
            analysis = ta_service.get_full_analysis(symbol)
            if "error" in analysis: return {"content": f"❌ Error: {analysis['error']}"}
            vwap = analysis['vwap']
            price = analysis['price']
            diff = (price - vwap) / vwap * 100
            status = "Bullish (Above VWAP)" if price > vwap else "Bearish (Below VWAP)"
            return {"content": f"💎 *{symbol} VWAP (S-Tier)*\n──────────────────\n💵 Price: ${price:.2f}\n📊 VWAP: ${vwap:.2f}\n📈 Offset: {diff:+.2f}%\n🎯 Signal: *{status}*"}

        if command == "score" and len(parts) > 1:
            symbol = parts[1].upper()
            from services.ta_engine import ta_engine
            res = ta_engine.compute_all(symbol)
            if "error" in res: return {"content": f"❌ Error: {res['error']}"}
            
            sig_lines = "\n".join([f"• {s['name']}: {s['signal']} ({s['strength']:+})" for s in res['signals']])
            report = (
                f"🎯 *TA Composite Score: {symbol}*\n"
                f"──────────────────\n"
                f"📈 Score: *{res['composite_score']}/100*\n"
                f"🏷️ Label: *{res['label']}*\n"
                f"🌐 Regime: {res['regime']}\n\n"
                f"*Signal Breakdown:*\n{sig_lines}"
            )
            return {"content": report}

        if command == "regime" and len(parts) > 1:
            symbol = parts[1].upper()
            from services.ta_engine import ta_engine
            res = ta_engine.compute_all(symbol)
            if "error" in res: return {"content": f"❌ Error: {res['error']}"}
            return {"content": f"🌐 *{symbol} Market Regime:* {res['regime']}"}

        if command == "sentiment" and len(parts) > 1:
            symbol = parts[1].upper()
            from services.sentiment_engine import sentiment_engine
            res = sentiment_engine.score_symbol(symbol)
            if "error" in res: return {"content": f"❌ Error: {res['error']}"}
            
            emoji = "🟢" if res['label'] == "BULLISH" else "🔴" if res['label'] == "BEARISH" else "🟡"
            report = (
                f"{emoji} *Sentiment Report: {symbol}*\n"
                f"──────────────────\n"
                f"📊 Score: *{res['score']}* (-1 to +1)\n"
                f"🏷️ Label: *{res['label']}*\n"
                f"🗞️ News count: {res.get('news_count', 0)}\n"
                f"💬 Social count: {res.get('social_count', 0)}\n\n"
                f"_Weighted by source reliability and time decay._"
            )
            return {"content": report}

        if command == "options" and len(parts) > 1:
            symbol = parts[1].upper()
            from services._legacy.options_engine import options_engine
            res = options_engine.get_chain_data(symbol)
            if not res: return {"content": f"❌ Error: Could not fetch options for {symbol}"}
            
            report = (
                f"🎰 *Options Flow: {symbol}*\n"
                f"──────────────────\n"
                f"🧠 Max Pain: *${res['max_pain']}*\n"
                f"💥 Total GEX: *{res['total_gex']:,.0f}*\n"
                f"⚖️ PCR (OI): *{res['pcr']}*\n"
                f"📍 Spot: ${res['spot_price']}\n\n"
                f"📅 Expiry: {res['expiration']}\n"
                f"_GEX is a proxy for market maker hedging pressure._"
            )
            return {"content": report}

        if command == "var" and len(parts) > 1:
            symbol = parts[1].upper()
            from services.risk_engine import risk_engine
            res = risk_engine.calculate_var(symbol)
            if "error" in res: return {"content": f"❌ {res['error']}"}
            report = (
                f"📉 *Value at Risk: {symbol}*\n"
                f"──────────────────\n"
                f"🎯 95% VaR: *{res['var_pct']}%*\n"
                f"💰 VaR/Share: ${res['var_usd_per_share']}\n"
                f"🔻 CVaR (Tail): {res['cvar_pct']}%\n"
                f"📊 Daily Vol: {res['daily_volatility']}%\n"
                f"📈 Annual Vol: {res['annualized_volatility']}%\n\n"
                f"_95% of the time, your daily loss won't exceed {abs(res['var_pct'])}%._"
            )
            return {"content": report}

        if command == "kelly":
            symbol = parts[1].upper() if len(parts) > 1 else None
            from services.risk_engine import risk_engine
            res = risk_engine.calculate_kelly(symbol)
            if "error" in res: return {"content": f"❌ {res['error']}"}
            report = (
                f"🎲 *Kelly Criterion: {res.get('symbol', 'PORTFOLIO')}*\n"
                f"──────────────────\n"
                f"📊 Win Rate: {res.get('win_rate', res.get('accuracy', 'N/A'))}%\n"
                f"🏆 Full Kelly: {res['kelly_full_pct']}%\n"
                f"✅ Half Kelly: *{res['kelly_half_pct']}%* (Recommended)\n"
                f"🏷️ Edge: *{res['label']}*\n\n"
                f"_{res.get('recommendation', '')}_"
            )
            return {"content": report}

        if command == "sharpe" and len(parts) > 1:
            symbol = parts[1].upper()
            from services.risk_engine import risk_engine
            res = risk_engine.calculate_sharpe(symbol)
            if "error" in res: return {"content": f"❌ {res['error']}"}
            report = (
                f"📐 *Sharpe Ratio: {symbol}*\n"
                f"──────────────────\n"
                f"📊 Sharpe: *{res['sharpe_ratio']}* ({res['label']})\n"
                f"📈 Return: {res['annualized_return']}%/yr\n"
                f"📉 Vol: {res['annualized_volatility']}%/yr\n\n"
                f"_Risk-free rate: {res['risk_free_rate']*100}%. > 1.0 = good, > 2.0 = excellent._"
            )
            return {"content": report}

        if command == "smartsize" and len(parts) > 1:
            symbol = parts[1].upper()
            from services.risk_engine import risk_engine
            quote = moomoo_service.get_stock_quote(symbol)
            price = quote.get("last_price") or 0
            if price <= 0: return {"content": f"❌ Could not get price for {symbol}."}
            res = risk_engine.smart_position_size(symbol, price)
            if "error" in res: return {"content": f"❌ {res['error']}"}
            report = (
                f"🧮 *Smart Position Size: {symbol}*\n"
                f"──────────────────\n"
                f"💵 Price: ${res['current_price']}\n"
                f"🎲 Kelly Risk: {res['kelly_risk_pct']}%\n"
                f"📉 VaR Stop: {res['var_stop_pct']}%\n"
                f"📦 Shares: *{res['recommended_shares']}*\n"
                f"💰 Cost: ${res['total_cost']}\n"
                f"🛡️ Stop: ${res['stop_loss_price']}\n\n"
                f"_Method: {res['method']}_"
            )
            return {"content": report}

        if command == "backtest" and len(parts) > 1:
            symbol = parts[1].upper()
            strategy = parts[2].lower() if len(parts) > 2 else "rsi"
            from services._legacy.backtest_engine import backtest_engine
            
            if strategy == "rsi":
                res = backtest_engine.backtest_rsi(symbol)
            elif strategy == "macd":
                res = backtest_engine.backtest_macd(symbol)
            elif strategy == "ema":
                res = backtest_engine.backtest_ema_crossover(symbol)
            else:
                return {"content": "❌ Unknown strategy. Use: rsi, macd, ema"}
                
            if not res.get("success"):
                return {"content": f"❌ Backtest failed: {res.get('error')}"}
                
            report = (
                f"🧪 *Backtest Results: {symbol}*\n"
                f"Strategy: {res['strategy']}\n"
                f"──────────────────\n"
                f"📈 Total Return: *{res['total_return_pct']}%*\n"
                f"⚖️ Benchmark: {res['benchmark_return_pct']}% (Buy & Hold)\n"
                f"🎯 Win Rate: {res['win_rate_pct']}%\n"
                f"🔻 Max DD: {res['max_drawdown_pct']}%\n"
                f"📐 Sharpe: {res['sharpe_ratio']}\n"
                f"📦 Trades: {res['total_trades']}\n\n"
                f"_Data: Past 365 days, 0.1% fees._"
            )
            return {"content": report}

        if command == "forecast" and len(parts) > 1:
            symbol = parts[1].upper()
            from services._legacy.ml_engine import ml_engine
            res = ml_engine.predict_price_lstm(symbol)
            if "error" in res: return {"content": f"❌ ML Error: {res['error']}"}
            
            icon = "🚀" if res['trend'] == "BULLISH" else "📉"
            report = (
                f"{icon} *ML Price Forecast: {symbol}*\n"
                f"──────────────────\n"
                f"💵 Current: ${res['current_price']}\n"
                f"🎯 Target (5d): *${res['predicted_price_5d']}*\n"
                f"📈 Change: *{res['predicted_change_pct']}%*\n"
                f"🏷️ Trend: {res['trend']}\n"
                f"🧠 Confidence: {res['confidence']}%\n\n"
                f"_Method: 30-day LSTM (PyTorch). 20 epochs training on-demand._"
            )
            return {"content": report}

        if command == "trend" and len(parts) > 1:
            symbol = parts[1].upper()
            from services._legacy.ml_engine import ml_engine
            res = ml_engine.predict_trend_random_forest(symbol)
            if "error" in res: return {"content": f"❌ ML Error: {res['error']}"}
            
            icon = "🟢" if res['trend'] == "BULLISH" else "🔴"
            report = (
                f"{icon} *ML Trend Analysis: {symbol}*\n"
                f"──────────────────\n"
                f"📈 Up Prob: *{res['up_probability']}%*\n"
                f"🏷️ Verdict: *{res['trend']}*\n"
                f"🧠 Confidence: {res['confidence']}%\n\n"
                f"_Features: RSI, SMA20/50, Volume Flow. Engine: Random Forest._"
            )
            return {"content": report}

        if command == "rrg":
            data = sector_service.calculate_rrg()
            if not data: return {"content": "❌ Error calculating RRG."}
            
            # Group by quadrant
            quads = {"LEADING": [], "IMPROVING": [], "WEAKENING": [], "LAGGING": []}
            for s in data:
                quads[s['quadrant']].append(s['symbol'])
            
            report = (
                f"🌀 *Sector Rotation (RRG)*\n"
                f"──────────────────\n"
                f"🚀 *Leading*: {', '.join(quads['LEADING']) if quads['LEADING'] else 'None'}\n"
                f"📈 *Improving*: {', '.join(quads['IMPROVING']) if quads['IMPROVING'] else 'None'}\n"
                f"📉 *Weakening*: {', '.join(quads['WEAKENING']) if quads['WEAKENING'] else 'None'}\n"
                f"🐢 *Lagging*: {', '.join(quads['LAGGING']) if quads['LAGGING'] else 'None'}\n\n"
                f"_Benchmark: SPY (14-day Rolling RS)_"
            )
            return {"content": report}

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

        if command == "darkpool" and len(parts) > 1:
            symbol = parts[1].upper()
            from services._legacy.darkpool_service import darkpool_service
            res = darkpool_service.get_summary(symbol)
            if res.get("status") == "NOT_CONFIGURED":
                return {"content": f"🐳 *Institutional Dark Pool: {symbol}*\n──────────────────\n{res['message']}"}
            return {"content": f"🐳 *Dark Pool Levels: {symbol}*\n" + "\n".join([f"• ${l['price']}: {l['volume']} shares" for l in res.get('levels', [])])}

        if command == "uoa" and len(parts) > 1:
            symbol = parts[1].upper()
            from services._legacy.uoa_service import uoa_service
            res = uoa_service.get_summary(symbol)
            if res.get("status") == "NOT_CONFIGURED":
                return {"content": f"🐙 *Options Flow (UOA): {symbol}*\n──────────────────\n{res['message']}"}
            return {"content": f"🐙 *UOA Risk: {symbol}*\n• Squeeze Risk: {res['squeeze_risk']}\n• Sweeps: {res['sweep_count']}\n• OTM Calls: {res['otm_call_count']}"}

        if command in ["sec", "insider"] and len(parts) > 1:
            symbol = parts[1].upper()
            from services._legacy.sec_filing_service import sec_service
            res = sec_service.get_summary(symbol)
            if res.get("status") == "NOT_CONFIGURED":
                return {"content": f"🏛️ *SEC Insider Tracking: {symbol}*\n──────────────────\n{res['message']}"}
            
            icon = "🟢" if "BULLISH" in res['insider_sentiment'] else "🔴" if "BEARISH" in res['insider_sentiment'] else "⚪"
            return {"content": f"🏛️ *Insider Activity: {symbol}*\n{icon} Sentiment: *{res['insider_sentiment']}*\n💰 Buys: {res['buy_count']}\n📉 Sells: {res['sell_count']}"}

        if command == "vol" and len(parts) > 1:
            symbol = parts[1].upper()
            res = parts[2].lower() if len(parts) > 2 else "1w"
            if res not in ["1d", "1w", "1m"]: res = "1w"
            report = vol_flow_service.get_signal_report(symbol, res)
            return {"content": report}

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
                from services.validation_service import validation_service
                is_blocked, message = validation_service.is_earnings_blocked(symbol)
                if is_blocked and "force" not in [p.lower() for p in parts]:
                    return {"content": f"🛡️ *EARNINGS BLOCK*\n{message}"}

                # 🔴 Rule: Risk Management Check (for Buy only)
                if side == "BUY":
                    # If price is 0, get it from quote
                    check_price = price if price > 0 else (moomoo_service.get_stock_quote(symbol).get("last_price") or 0)
                    risk_check = risk_service.calculate_position_size(symbol, check_price)
                    if "error" in risk_check:
                        return {"content": f"🛡️ *RISK BLOCK*\n{risk_check['error']}"}
                    
                    if qty > risk_check["recommended_shares"]:
                        return {"content": f"🛡️ *POSITION SIZE WARNING*\nRecommended max: {risk_check['recommended_shares']} shares.\nYou requested: {qty}.\nTo override, use `!buy {symbol} {qty} {price} force`."}

                # 🔴 Rule: Treasurer Approval Layer
                if "force" not in [p.lower() for p in parts]:
                    check_price = price if price > 0 else (moomoo_service.get_stock_quote(symbol).get("last_price") or 0)
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
                
                # 🔴 Rule: Check earnings before prediction
                from services.validation_service import validation_service
                is_blocked, message = validation_service.is_earnings_blocked(symbol)
                if is_blocked and not force:
                    return {"content": f"⚠️ *PREDICTION BLOCKED*\n{message}\nTo override, use `!predict {symbol} ... force`."}
                
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
                    force=force,
                    prediction_tag=body.get("prediction_tag")
                )
                if res["success"]:
                    from services.signal_aggregator import signal_aggregator
                    consensus = signal_aggregator.get_consensus(symbol)
                    conf = consensus.get("confidence_score", 50)
                    label = consensus.get("label", "NEUTRAL")
                    
                    warn_text = "\n\n⚠️ *Warnings:*\n" + "\n".join(res["warnings"]) if res.get("warnings") else ""
                    return {
                        "content": (
                            f"🔮 *Prediction #{res['prediction_id']} Recorded*\n"
                            f"──────────────────\n"
                            f"{symbol} *{direction}* @ ${res['entry_price']:.2f}\n"
                            f"🧠 Consensus: *{label}* ({conf}%)\n"
                            f"📣 Catalyst: {catalyst}{warn_text}"
                        )
                    }
                
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
            
            # Add win/loss streaks
            streak = stats.get('current_streak', 0)
            streak_text = f"🔥 Streak: {streak} WINS" if streak > 0 else f"❄️ Streak: {abs(streak)} LOSSES" if streak < 0 else ""
            
            return {
                "content": (
                    f"🎯 *Prediction Scorecard*\n"
                    f"──────────────────\n"
                    f"📈 Total Resolved: {stats['total_resolved']}\n"
                    f"🎯 Win Rate: {stats['accuracy_pct']}%\n"
                    f"{streak_text}\n\n"
                    f"*By Category:*\n{cats}\n\n"
                    f"_Tip: Use !postmortem <id> to see why a specific call failed._"
                )
            }

        if command == "postmortem" and len(parts) > 1:
            try:
                pid = int(parts[1])
                db = SessionLocal()
                prediction = db.query(Prediction).filter(Prediction.id == pid).first()
                if not prediction or not prediction.postmortem:
                    return {"content": f"❌ No post-mortem found for Prediction #{pid}."}
                
                pm = prediction.postmortem
                correct = ", ".join(pm.get("correct_signals", [])) or "None"
                incorrect = ", ".join(pm.get("incorrect_signals", [])) or "None"
                fail = pm.get("primary_failure", "N/A")
                
                status_icon = "✅" if prediction.outcome == "RIGHT" else "❌"
                report = (
                    f"{status_icon} *Post-Mortem: Prediction #{pid} ({prediction.symbol})*\n"
                    f"──────────────────\n"
                    f"🏷️ Outcome: *{prediction.outcome}* ({prediction.actual_move_pct:.2f}%)\n"
                    f"🧊 Data Quality: {pm.get('data_quality', 'N/A')}\n\n"
                    f"🎯 *What Worked:*\n{correct}\n\n"
                    f"🚩 *What Lied:*\n{incorrect}\n\n"
                    f"💥 *Primary Failure:* {fail}\n\n"
                    f"_Post-mortem auto-generated upon resolution._"
                )
                return {"content": report}
            except Exception as e:
                return {"content": f"⚠️ Error: {str(e)}"}
            finally:
                db.close()

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
                    f"• !vwap SYMBOL — S-tier trend check\n"
                    f"• !vol SYMBOL [1d|1w|1m] — volume flow\n"
                    f"• !tradelog [stats] — view history\n"
                    f"• !strategy — view evolution stats\n"
                    f"• !watchlist — show watchlist\n"
                    f"• !add SYMBOL — add to watchlist\n"
                    f"• !remove SYMBOL — remove from watchlist\n"
                    f"• !news — trigger news scrape\n"
                    f"• !status — server health"
                )
            }

        if command == "paper":
            portfolio = paper_trading_service.get_portfolio()
            balance = paper_trading_service.get_performance()
            cash = f"${balance.get('current_balance', 0):.2f}"
            if not portfolio:
                return {"content": f"📄 *Paper Portfolio*\n──────────────────\n💵 Balance: {cash}\nNo open positions."}
            lines = [f"• {p['symbol']}: {p['quantity']} shares @ ${p['entry_price']:.2f} | P&L: {p.get('pnl_pct', 0):+.2f}%" for p in portfolio]
            return {"content": f"📄 *Paper Portfolio*\n──────────────────\n💵 Balance: {cash}\n\n" + "\n".join(lines)}

        if command == "paperbuy" and len(parts) >= 3:
            symbol = parts[1].upper()
            try:
                qty = float(parts[2])
                price = float(parts[3]) if len(parts) > 3 else None
                if not price:
                    quote = moomoo_service.get_stock_quote(symbol)
                    price = quote.get("last_price") or 0
                if not price:
                    return {"content": f"❌ Could not fetch price for {symbol}."}
                res = paper_trading_service.open_trade(symbol, "BUY", qty, price)
                if res.get("success"):
                    return {"content": f"📄 *Paper BUY*\n✅ {qty} {symbol} @ ${price:.2f}\n🛡️ Stop: ${res.get('stop_loss', 0):.2f}\n_No real money used._"}
                return {"content": f"❌ {res.get('error', 'Trade failed')}"}
            except ValueError:
                return {"content": "⚠️ Usage: `!paperbuy SYMBOL QTY [PRICE]`"}

        if command == "papersell" and len(parts) >= 3:
            symbol = parts[1].upper()
            try:
                qty = float(parts[2])
                price = float(parts[3]) if len(parts) > 3 else None
                if not price:
                    quote = moomoo_service.get_stock_quote(symbol)
                    price = quote.get("last_price") or 0
                portfolio = paper_trading_service.get_portfolio()
                trade = next((p for p in portfolio if p['symbol'] == symbol), None)
                if not trade:
                    return {"content": f"❌ No open paper position for {symbol}."}
                res = paper_trading_service.close_trade(trade['id'], price, reason="manual_sell")
                if res.get("success"):
                    return {"content": f"📄 *Paper SELL*\n✅ {symbol} closed @ ${price:.2f}\n📈 P&L: {res.get('pnl_pct', 0):+.2f}% (${res.get('pnl_usd', 0):+.2f})\n_No real money used._"}
                return {"content": f"❌ {res.get('error', 'Close failed')}"}
            except ValueError:
                return {"content": "⚠️ Usage: `!papersell SYMBOL QTY [PRICE]`"}

        if command == "paperstats":
            perf = paper_trading_service.get_performance()
            return {"content": (
                f"📊 *Paper Trading Stats*\n"
                f"──────────────────\n"
                f"💰 Balance: ${perf.get('current_balance', 0):.2f}\n"
                f"📈 Total P&L: ${perf.get('total_pnl_usd', 0):+.2f} ({perf.get('total_pnl_pct', 0):+.2f}%)\n"
                f"🎯 Win Rate: {perf.get('win_rate', 0):.0f}%\n"
                f"🔢 Total Trades: {perf.get('trade_count', 0)}\n"
                f"📂 Open: {perf.get('open_count', 0)}\n\n"
                f"_Simulated trades only. No real money._"
            )}

        return {"content": "Unknown command. Try !help"}

    except Exception as e:
        logger.error(f"[Webhook] Error: {e}")
        return {"content": f"⚠️ Error: {str(e)}"}

# Define startup time at the top
startup_time = time.time()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3001)
