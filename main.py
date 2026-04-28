import os
import time
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from loguru import logger

from core.database import init_db, get_db, UserWatchlist
from core.scheduler import scheduler
from services.moomoo_service import moomoo_service
from services.notifications import notification_queue

app = FastAPI(title="MooPredict AI API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Events ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup_event():
    logger.info("🚀 Starting MooPredict Python Backend...")
    try:
        init_db()
        moomoo_service.connect()
        scheduler.init()
        logger.info("✅ Startup sequence complete.")
    except Exception as e:
        logger.error(f"❌ Startup failure: {e}")

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Stopping MooPredict...")
    scheduler.shutdown()
    moomoo_service.close()

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
def get_positions():
    """Direct API access to current positions."""
    return moomoo_service.get_positions()

@app.get("/api/balance")
def get_balance():
    """Direct API access to account balance."""
    return moomoo_service.get_balance()

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

# ─── Webhook (Command Handler) ──────────────────────────────────────────────
@app.post("/webhook")
async def handle_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        text = (body.get("text") or body.get("content") or "").strip()
        parts = text.split()
        if not parts:
            return {"content": "Empty command."}
        
        command = parts[0].lower()
        logger.info(f"[Webhook] Command: {command} | Args: {parts[1:]}")

        if command in ["!checkstatus", "!positions", "!pos"]:
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

        if command == "!watchlist":
            symbols = db.query(UserWatchlist).all()
            list_str = "\n".join([f"• {s.symbol}" for s in symbols]) if symbols else "Watchlist is empty."
            return {"content": f"📋 *Watchlist*\n──────────────\n{list_str}"}

        if command == "!add" and len(parts) > 1:
            symbol = parts[1].upper()
            existing = db.query(UserWatchlist).filter(UserWatchlist.symbol == symbol).first()
            if not existing:
                db.add(UserWatchlist(symbol=symbol))
                db.commit()
            return {"content": f"✅ Added *{symbol}* to watchlist."}

        if command in ["!remove", "!rm"] and len(parts) > 1:
            symbol = parts[1].upper()
            db.query(UserWatchlist).filter(UserWatchlist.symbol == symbol).delete()
            db.commit()
            return {"content": f"🗑️ Removed *{symbol}* from watchlist."}

        if command == "!status":
            wl_count = db.query(UserWatchlist).count()
            status_text = (
                f"✅ *Server Online (Python)*\n"
                f"📋 Watchlist: {wl_count} symbols\n"
                f"🔗 Moomoo: {'connected' if moomoo_service.is_connected else 'not connected'}"
            )
            return {"content": status_text}

        if command == "!news":
            from services.news_scraper import news_scraper
            count = news_scraper.run()
            return {"content": f"🚀 News scrape triggered! Found {count} new articles."}

        if command == "!x":
            from services.x_scraper import x_scraper
            count = x_scraper.run()
            return {"content": f"🐦 X scrape triggered! Found {count} new posts."}

        if command == "!reddit":
            from services.reddit_scraper import reddit_scraper
            count = reddit_scraper.run()
            return {"content": f"🤖 Reddit scrape triggered! Found {count} new posts."}

        if command == "!help":
            return {
                "content": (
                    f"*Commands:*\n"
                    f"• !checkstatus — positions & balance\n"
                    f"• !watchlist — show watchlist\n"
                    f"• !add SYMBOL — add to watchlist\n"
                    f"• !remove SYMBOL — remove from watchlist\n"
                    f"• !news — trigger news scrape\n"
                    f"• !x — trigger X check\n"
                    f"• !reddit — trigger Reddit check\n"
                    f"• !status — server health"
                )
            }

        return {"content": "Unknown command. Try !help"}

    except Exception as e:
        logger.error(f"[Webhook] Error: {e}")
        return {"content": f"⚠️ Error: {str(e)}"}

startup_time = time.time()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3001)
