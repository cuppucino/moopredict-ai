import sys
import os
import time
import schedule
from loguru import logger

# Add root directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.prediction_service import prediction_service
from services.notifications import notification_queue

def run_resolution():
    logger.info("[Resolver] Running scheduled prediction resolution...")
    try:
        resolved = prediction_service.resolve_pending_predictions()
        
        if not resolved:
            logger.info("[Resolver] No predictions to resolve today.")
            return

        for r in resolved:
            outcome_emoji = "✅" if r['outcome'] == "RIGHT" else "❌"
            msg = (
                f"{outcome_emoji} *PREDICTION RESOLVED: {r['symbol']}*\n\n"
                f"Result: {r['outcome']}\n"
                f"Move: {r['move_pct']:.2f}%\n\n"
                f"Apprentice Request: Please review this outcome. Tell me WHY I was {r['outcome'].lower()} so I can learn from this trade."
            )
            notification_queue.enqueue(msg, level="info", category="feedback")
            
        # Also send a weekly-style summary if there were many
        stats = prediction_service.get_stats()
        summary_msg = (
            f"📊 *Current Prediction Stats*\n"
            f"Total Resolved: {stats.get('total_resolved')}\n"
            f"Accuracy: {stats.get('accuracy_pct')}%\n\n"
            f"Category Breakdown:\n" + "\n".join([f"- {k}: {v}%" for k,v in stats.get('by_category', {}).items()])
        )
        notification_queue.enqueue(summary_msg, level="info", category="general")
        
    except Exception as e:
        logger.error(f"[Resolver] Critical error in resolution loop: {e}")

if __name__ == "__main__":
    logger.info("[Resolver] Starting Prediction Resolution Service...")
    
    # Run once at startup
    run_resolution()

    # Schedule to run every 15 minutes. Previously every 12 hours which
    # meant daily-cadence predictions could sit unresolved for hours past
    # their deadline. 15 min caps resolution latency for any timeframe.
    schedule.every(15).minutes.do(run_resolution)

    while True:
        schedule.run_pending()
        time.sleep(60)
