import pytz
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from services.news_scraper import news_scraper
from services.x_scraper import x_scraper
from services.reddit_scraper import reddit_scraper
from services.daily_briefing import briefing_service
from services.notifications import notification_queue
from services._legacy.options_flow import options_service
from services.trailing_stop_service import trailing_stop_service
from services.strategy_service import strategy_service
from core.database import SessionLocal, UserWatchlist
from services.heartbeat_monitor import heartbeat_monitor
from services.macro_calendar_service import macro_service
from services._legacy.ml_service import ml_service
from services._legacy.pattern_analyzer import pattern_analyzer
from services.watchdog import watchdog_service

def _run_job_with_timeout(func, timeout_sec: float = 30.0):
    """Run a scheduler job in a non-blocking thread pool executor with a timeout."""
    def wrapper(*args, **kwargs):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(func, *args, **kwargs)
            return future.result(timeout=timeout_sec)
        except TimeoutError:
            logger.error(f"[Scheduler] Job {func.__name__ if hasattr(func, '__name__') else str(func)} timed out after {timeout_sec}s")
            return None
        except Exception as e:
            logger.error(f"[Scheduler] Job {func.__name__ if hasattr(func, '__name__') else str(func)} failed: {e}")
            return None
        finally:
            executor.shutdown(wait=False)
    if hasattr(func, '__name__'):
        wrapper.__name__ = func.__name__
    return wrapper

class Scheduler:
    def __init__(self):
        # Force UTC to avoid local timezone offsets and configure misfire grace time to prevent skipped checks
        job_defaults = {
            'coalesce': True,
            'misfire_grace_time': 3600
        }
        self.scheduler = BackgroundScheduler(timezone=pytz.utc, job_defaults=job_defaults)
        self.is_running = False

    def init(self):
        """Initialize and start cron jobs."""
        if self.is_running:
            return

        logger.info("[Scheduler] Initializing Python cron jobs...")

        # Moomoo background reconnect — runs every 5 min, ALL hours. Two failure modes:
        #   1) Hard disconnect (is_connected=False): reconnect.
        #   2) Silent wedge: socket reports connected but the stream is dead (RemoteClose
        #      leaves is_connected=True — observed 2026-07-09 stale 25h, and 2026-07-18
        #      wedged all weekend). We ACTIVELY probe a canary quote and rebuild if it fails.
        # The probe runs ALL hours (not just market hours): moomoo returns last close when
        # closed, so a weekend wedge otherwise rides uncorrected until Monday AND every
        # blocking moomoo call (hourly heartbeat's get_positions/get_balance) piles up on the
        # dead connection with no timeout, starving the HTTP threadpool until /api/health
        # itself hangs. The probe is wrapped in an 8s timeout so a wedged connection fails
        # FAST and self-heals within one tick instead of hanging a thread.
        def _probe_quote_with_timeout(timeout_s: float = 8.0) -> bool:
            import concurrent.futures
            from services.moomoo_service import moomoo_service
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(moomoo_service.get_stock_quote, "SPY")
            try:
                q = fut.result(timeout=timeout_s)
                return bool(q and q.get("last_price", 0) > 0)
            finally:
                ex.shutdown(wait=False)  # never block on a hung SDK call

        def _moomoo_reconnect_tick():
            import concurrent.futures
            from services.moomoo_service import moomoo_service
            if not moomoo_service.is_connected:
                logger.info("[Scheduler] Moomoo disconnected — attempting background reconnect...")
                try:
                    ok = moomoo_service.connect()
                    if ok:
                        logger.success("[Scheduler] Moomoo background reconnect succeeded.")
                except Exception as e:
                    logger.warning(f"[Scheduler] Moomoo reconnect attempt failed: {e}")
                return

            # Connected — actively verify with a timeout-bounded canary quote. Success
            # re-stamps freshness (inside get_stock_quote); a failed/timed-out probe means
            # a wedged connection -> full close()+connect() rebuild.
            try:
                healthy = _probe_quote_with_timeout(8.0)
            except concurrent.futures.TimeoutError:
                logger.warning("[Scheduler] Moomoo quote probe TIMED OUT (wedged connection) — forcing rebuild.")
                healthy = False
            except Exception as e:
                logger.warning(f"[Scheduler] Moomoo quote probe errored: {e} — forcing rebuild.")
                healthy = False
            if healthy:
                return
            try:
                moomoo_service.close()
                ok = moomoo_service.connect()
                if ok:
                    logger.success("[Scheduler] Moomoo wedged-connection rebuild succeeded.")
                else:
                    logger.warning("[Scheduler] Moomoo wedged-connection rebuild failed.")
            except Exception as e:
                logger.warning(f"[Scheduler] Moomoo wedged-connection rebuild error: {e}")
        self.scheduler.add_job(
            _moomoo_reconnect_tick,
            CronTrigger.from_crontab("*/5 * * * *", timezone=pytz.utc),
            id="moomoo_reconnect_tick",
            max_instances=1,
            replace_existing=True,
        )

        # Morning brief — Phase 6. Runs at 00:00 UTC = 08:00 MYT every weekday.
        # Scans last 24h news + VIP tweets, scores ETF universe, adds regime banner,
        # writes markdown to data/morning_briefs/YYYY-MM-DD.md.
        #
        # ⚠️ APScheduler day-of-week gotcha (bug found & fixed 2026-07-13):
        # from_crontab does NOT remap the DOW field to Unix-cron numbering — it reads
        # numeric DOW as 0=Mon..6=Sun. So a Unix-style "1-5" fires Tue–Sat (not Mon–Fri)
        # and "0" fires Mon (not Sun). ALWAYS use names (mon-fri / sun) in DOW here.
        # This silently skipped every Monday brief and wasted a Saturday run for weeks.
        def _run_morning_brief():
            from services.morning_brief_engine import morning_brief_engine
            try:
                morning_brief_engine.generate_brief()
            except Exception as e:
                logger.error(f"[Scheduler] Morning brief generation failed: {e}")
        self.scheduler.add_job(
            _run_morning_brief,
            CronTrigger.from_crontab("0 0 * * mon-fri", timezone=pytz.utc),
            id="morning_brief_job",
            max_instances=1,
            # Lid-close sleep can make the Mac miss 08:00 MYT by hours (2026-07-20: missed
            # by 1:47 > default 1h grace -> skipped to next day). A late brief is far better
            # than none — allow firing up to 6h late on wake.
            misfire_grace_time=6 * 3600,
            replace_existing=True,
        )

        # Push the brief into openclaw's workspace 5 min after generation, so kf's
        # morning prompt is just "run today's session" — no wall-of-text pasting.
        # One-way: server writes, openclaw reads (per the research-pipeline rule).
        def _push_brief_to_openclaw():
            from pathlib import Path
            from datetime import date
            src = Path(f"/Users/admin/moopredict-ai/data/morning_briefs/{date.today().isoformat()}.md")
            dst_dir = Path("/Users/admin/.openclaw/workspace/morning_briefs")
            try:
                if src.exists():
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    content = src.read_text()
                    (dst_dir / src.name).write_text(content)
                    (dst_dir / "latest.md").write_text(content)
                    logger.info(f"[Scheduler] Brief pushed to openclaw workspace: {src.name}")
                else:
                    logger.warning(f"[Scheduler] Brief push skipped — {src} not found")
            except Exception as e:
                logger.error(f"[Scheduler] Brief push failed: {e}")
        self.scheduler.add_job(
            _push_brief_to_openclaw,
            CronTrigger.from_crontab("5 0 * * mon-fri", timezone=pytz.utc),
            id="brief_push_openclaw_job",
            max_instances=1,
            misfire_grace_time=6 * 3600,  # late push still useful (see morning_brief_job)
            replace_existing=True,
        )

        # Scrapers
        self.scheduler.add_job(news_scraper.run, CronTrigger.from_crontab("0 * * * *", timezone=pytz.utc), id="news_scrape_job")
        self.scheduler.add_job(x_scraper.run, CronTrigger.from_crontab("*/15 * * * *", timezone=pytz.utc), id="x_scrape_job")
        self.scheduler.add_job(reddit_scraper.run, CronTrigger.from_crontab("*/30 * * * *", timezone=pytz.utc), id="reddit_scrape_job")

        # Briefings (UTC)
        # 02:00 UTC = 10:00 AM MYT
        self.scheduler.add_job(briefing_service.generate_morning_briefing, CronTrigger.from_crontab("0 2 * * *", timezone=pytz.utc), id="morning_briefing_job")
        
        # 13:00 UTC = 09:00 PM MYT (Pre-market prep)
        self.scheduler.add_job(briefing_service.generate_premarket_prep, CronTrigger.from_crontab("0 13 * * *", timezone=pytz.utc), id="premarket_prep_job")
        
        # Market Reminders (UTC)
        # 13:00 UTC = 9:00 PM MYT
        self.scheduler.add_job(lambda: notification_queue.enqueue("🚨 *US Market opens in 30 minutes!* ⏳", "alert", category="news"), CronTrigger.from_crontab("0 13 * * mon-fri", timezone=pytz.utc), id="market_open_soon_job")
        
        # 13:30 UTC = 9:30 PM MYT
        self.scheduler.add_job(lambda: notification_queue.enqueue("🔔 *US Market is OPEN!* 📈", "info", category="news"), CronTrigger.from_crontab("30 13 * * mon-fri", timezone=pytz.utc), id="market_open_job")
        
        # 20:55 UTC = 04:55 AM MYT (5 mins before close)
        self.scheduler.add_job(lambda: notification_queue.enqueue("🔔 *US Market is closing in 5 minutes!* 📉", "warning", category="news"), CronTrigger.from_crontab("55 20 * * mon-fri", timezone=pytz.utc), id="market_close_soon_job")

        # 21:00 UTC = 05:00 AM MYT
        self.scheduler.add_job(briefing_service.generate_eod_summary, CronTrigger.from_crontab("0 21 * * *", timezone=pytz.utc), id="eod_summary_job")

        # Price Alerts (Every 5 minutes)
        from services.alert_service import alert_service
        self.scheduler.add_job(
            _run_job_with_timeout(alert_service.check_alerts, 30.0),
            CronTrigger.from_crontab("*/5 * * * *", timezone=pytz.utc),
            id="price_alert_job",
            max_instances=2
        )

        # Trailing Stops (Every 5 minutes during market hours)
        self.scheduler.add_job(
            _run_job_with_timeout(trailing_stop_service.update_stops, 30.0),
            CronTrigger.from_crontab("*/5 13-21 * * mon-fri", timezone=pytz.utc),
            id="trailing_stop_job",
            max_instances=2
        )

        # TA Snapshots (Every hour during market hours)
        from services.ta_engine import ta_engine
        def ta_snapshot_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                for sym in symbols:
                    ta_engine.save_snapshot(sym)
            finally:
                db.close()
        
        self.scheduler.add_job(ta_snapshot_job, CronTrigger.from_crontab("0 13-21 * * mon-fri", timezone=pytz.utc), id="ta_snapshot_job")

        # Sentiment Snapshots (Every hour during market hours)
        from services.sentiment_engine import sentiment_engine
        def sentiment_snapshot_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                for sym in symbols:
                    sentiment_engine.save_snapshot(sym)
            finally:
                db.close()
        
        self.scheduler.add_job(sentiment_snapshot_job, CronTrigger.from_crontab("0 13-21 * * mon-fri", timezone=pytz.utc), id="sentiment_snapshot_job")

        # Options Snapshots (Every 4 hours during market hours - options data is heavy)
        from services._legacy.options_engine import options_engine
        def options_snapshot_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                for sym in symbols:
                    options_engine.save_snapshot(sym)
            finally:
                db.close()
        
        self.scheduler.add_job(options_snapshot_job, CronTrigger.from_crontab("0 14,18,22 * * mon-fri", timezone=pytz.utc), id="options_snapshot_job")

        # PCR Monitor (Every hour)
        def pcr_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                if symbols:
                    # options_service.monitor_pcr_spikes(symbols)
                    logger.warning("[Scheduler] monitor_pcr_spikes not implemented. Skipping.")
            finally:
                db.close()
        
        self.scheduler.add_job(pcr_job, CronTrigger.from_crontab("0 * * * *", timezone=pytz.utc), id="pcr_spike_job")

        # Prediction Auto-Resolve (Every 6 hours)
        from services.prediction_service import prediction_service
        self.scheduler.add_job(prediction_service.resolve_pending_predictions, CronTrigger.from_crontab("0 */6 * * *", timezone=pytz.utc), id="prediction_resolve_job")

        # --- OpenClaw Autonomous Triggers ---
        from services.openclaw_service import openclaw_service
        
        # 1. Premarket Sweep (12:00 UTC, Mon-Fri)
        self.scheduler.add_job(
            _run_job_with_timeout(lambda: openclaw_service.trigger_agent(
                "premarket",
                "Run the daily pre-market sweep using today's morning brief and current news catalysts. "
                "Post AT LEAST ONE prediction EVERY day — your single best setup — even on weak days. This is a "
                "paper experiment: every prediction is free data, so we never skip a day. "
                "For each: FIRST validate via POST /api/predictions/validate, THEN create it via POST /api/predictions. "
                "Tag every prediction with your HONEST conviction (high / medium / low via the confidence field). On a "
                "weak day, post your best pick tagged LOW conviction — do NOT inflate conviction to justify posting it. "
                "Post MORE than one only when multiple setups genuinely clear high conviction. "
                "A prediction exists ONLY when it is in the system database — memory or journal entries do NOT count. "
                "Do not assign prediction IDs yourself (the system assigns them). Do not resolve predictions yourself "
                "(the resolver does that)."
            ), 180.0),
            CronTrigger.from_crontab("0 12 * * mon-fri", timezone=pytz.utc),
            id="openclaw_premarket_sweep",
            max_instances=2,
            # US session runs 13:30-20:00 UTC — a sweep fired up to 4h late (16:15 UTC)
            # is still mid-session and valuable; beyond that, skip to tomorrow.
            misfire_grace_time=4 * 3600,
        )

        # 1b. Auto-Draft — deterministic daily prediction (12:15 UTC = 20:15 MYT, Mon-Fri).
        # Runs 15 min after openclaw's sweep so the agent gets first crack; then the
        # system GUARANTEES at least one real prediction/day from the brief's best setup.
        # openclaw proved unreliable at actually POSTing (journals fabricated ledgers
        # instead — see services/auto_draft_engine.py), so execution moves to the system;
        # openclaw's role shrinks to reviewing/adjusting. Tagged [AUTO_DRAFT] for Phase 7.
        def _run_auto_draft():
            from services.auto_draft_engine import auto_draft_engine
            try:
                auto_draft_engine.generate_daily_draft()
            except Exception as e:
                logger.error(f"[Scheduler] Auto-draft failed: {e}")
        self.scheduler.add_job(
            _run_auto_draft,
            CronTrigger.from_crontab("15 12 * * mon-fri", timezone=pytz.utc),
            id="auto_draft_job",
            max_instances=1,
            misfire_grace_time=4 * 3600,  # late daily draft still lands mid-session (see sweep)
        )

        # 1c. Position-Watch — daily prediction on the user's ACTUAL held positions
        # (12:20 UTC = 20:20 MYT). Deterministic, scoped to moomoo positions, tagged
        # [POSITION_WATCH] as its own track. See services/position_watch_engine.py.
        def _run_position_watch():
            from services.position_watch_engine import position_watch_engine
            try:
                position_watch_engine.generate_daily()
            except Exception as e:
                logger.error(f"[Scheduler] Position-watch failed: {e}")
        self.scheduler.add_job(
            _run_position_watch,
            CronTrigger.from_crontab("20 12 * * mon-fri", timezone=pytz.utc),
            id="position_watch_job",
            max_instances=1,
            misfire_grace_time=4 * 3600,
            replace_existing=True,
        )

        # 2. Intra-day Decision/Monitor Cycle (Every 30 minutes, 13:00 to 20:30 UTC, Mon-Fri)
        self.scheduler.add_job(
            _run_job_with_timeout(lambda: openclaw_service.trigger_agent(
                "decision",
                "Check open predictions for target/stop hits and resolve any that crossed thresholds. "
                "Do NOT create new predictions in this cycle — only the 12:00 UTC pre-market sweep creates new predictions. "
                "If no resolutions needed, return silently."
            ), 60.0),
            CronTrigger.from_crontab("*/30 13-20 * * mon-fri", timezone=pytz.utc),
            id="openclaw_decision_cycle",
            max_instances=2
        )

        # 3. Post-market Analysis (20:30 UTC, Mon-Fri)
        self.scheduler.add_job(
            _run_job_with_timeout(lambda: openclaw_service.trigger_agent(
                "postmarket",
                "Run post-market analysis and write daily trading lessons."
            ), 120.0),
            CronTrigger.from_crontab("30 20 * * mon-fri", timezone=pytz.utc),
            id="openclaw_postmarket_analysis",
            max_instances=2
        )


        # Weekly Outlook (Sunday 02:00 UTC = 10:00 AM MYT)
        from services._legacy.outlook_service import outlook_service
        self.scheduler.add_job(outlook_service.generate_weekly_outlook, CronTrigger.from_crontab("0 2 * * sun", timezone=pytz.utc), id="weekly_outlook_job")

        # Strategy Evolution (Sunday 03:00 UTC = 11:00 AM MYT)
        self.scheduler.add_job(strategy_service.evolve, CronTrigger.from_crontab("0 3 * * sun", timezone=pytz.utc), id="strategy_evolution_job")

        # --- Heartbeat Monitoring ---
        # 1. Full Check every 15 min during market hours (13:30-21:00 UTC)
        self.scheduler.add_job(heartbeat_monitor.run_full_check, CronTrigger.from_crontab("*/15 13-21 * * mon-fri", timezone=pytz.utc), id="heartbeat_check_job")
        
        # 2. Daily P&L Report (21:00 UTC)
        self.scheduler.add_job(heartbeat_monitor.generate_daily_report, CronTrigger.from_crontab("0 21 * * mon-fri", timezone=pytz.utc), id="heartbeat_daily_report")
        
        # 3. Pre-market Scan (13:00 UTC)
        self.scheduler.add_job(heartbeat_monitor.generate_premarket_scan, CronTrigger.from_crontab("0 13 * * mon-fri", timezone=pytz.utc), id="heartbeat_premarket_scan")

        # 4. Off-hours Heartbeat (Every hour)
        self.scheduler.add_job(heartbeat_monitor.run_full_check, CronTrigger.from_crontab("0 * * * *", timezone=pytz.utc), id="heartbeat_offhours_job")

        # --- Maintenance & Intelligence Jobs ---
        
        # 1. Daily Accuracy Report (22:00 UTC)
        from core.database import Prediction
        from datetime import timedelta
        def accuracy_report_job():
            db = SessionLocal()
            try:
                from services.prediction_service import prediction_service
                # Resolve pending first for current data
                prediction_service.resolve_pending_predictions()
                
                yesterday = datetime.utcnow() - timedelta(days=1)
                results = db.query(Prediction).filter(Prediction.resolved_at >= yesterday).all()
                
                if not results:
                    return
                
                correct = len([r for r in results if r.outcome == 'RIGHT'])
                total = len(results)
                win_rate = (correct / total * 100) if total > 0 else 0
                
                msg = (
                    f"📊 *Daily Accuracy Report*\n"
                    f"───────────────────────────\n"
                    f"Total Predictions: {total}\n"
                    f"✅ Correct: {correct}\n"
                    f"🎯 Win Rate: {win_rate:.1f}%"
                )
                notification_queue.enqueue(msg, level="info", category="general")
            finally:
                db.close()
        
        from datetime import datetime
        self.scheduler.add_job(accuracy_report_job, CronTrigger.from_crontab("0 22 * * *", timezone=pytz.utc), id="accuracy_report_job")

        # 2. Weekly Macro Calendar (Sunday 04:00 UTC)
        def macro_calendar_job():
            briefing = macro_service.generate_weekly_briefing()
            notification_queue.enqueue(briefing, level="info", category="news")
            
        self.scheduler.add_job(macro_calendar_job, CronTrigger.from_crontab("0 4 * * sun", timezone=pytz.utc), id="macro_calendar_job")

        # 3. Weekly ML Retrain (Sunday 05:00 UTC)
        def model_retrain_job():
            results = ml_service.retrain_all()
            msg = (
                f"🤖 *Weekly ML Retrain*\n"
                f"───────────────────────────\n"
                f"• LSTM: {results.get('lstm')}\n"
                f"• Random Forest: {results.get('random_forest')}\n"
                f"• Data points: {results.get('data_points')}\n"
                f"⏱️ Duration: {results.get('duration_sec')}s"
            )
            notification_queue.enqueue(msg, level="info", category="system")

        self.scheduler.add_job(model_retrain_job, CronTrigger.from_crontab("0 5 * * sun", timezone=pytz.utc), id="model_retrain_job")

        # 4. Weekly Pattern Review (Sunday 06:00 UTC)
        def pattern_review_job():
            analysis = pattern_analyzer.analyze_effectiveness()
            if "error" in analysis: return
            
            msg = (
                f"📈 *Weekly Pattern Review*\n"
                f"───────────────────────────\n"
                f"Total Reviewed: {analysis.get('total')}\n"
                f"✅ Effective: {analysis.get('effective')}\n"
                f"⚠️ Needs Review: {analysis.get('needs_review')}\n"
            )
            notification_queue.enqueue(msg, level="info", category="general")

        self.scheduler.add_job(pattern_review_job, CronTrigger.from_crontab("0 6 * * sun", timezone=pytz.utc), id="pattern_review_job")

        # Daily Track Record evaluation (21:30 UTC Mon-Fri)
        from services import track_record
        self.scheduler.add_job(track_record.run_daily_job, CronTrigger.from_crontab("30 21 * * mon-fri", timezone=pytz.utc), id="track_record_daily")

        self.scheduler.start()
        
        # Trigger an initial heartbeat check in a background thread to ensure fresh startup state
        import threading
        threading.Thread(target=heartbeat_monitor.run_full_check, daemon=True, name="startup_heartbeat_check").start()
        
        watchdog_service.start()
        self.is_running = True
        logger.info("[Scheduler] Jobs scheduled and running.")

    def shutdown(self):
        """Gracefully shutdown the scheduler."""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("[Scheduler] Shutdown complete.")

scheduler = Scheduler()
