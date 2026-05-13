from typing import Dict, List, Optional
from loguru import logger
from core.database import Prediction

class PredictionPostmortem:
    def analyze(self, prediction: Prediction) -> Dict:
        """
        Compare prediction-time signals vs actual outcome.
        Returns a breakdown of signal attribution.
        """
        try:
            summary = prediction.signal_summary or {}
            outcome = prediction.outcome # RIGHT | WRONG
            direction = prediction.direction # UP | DOWN
            
            # Simple attribution logic
            attribution = {
                "correct_signals": [],
                "incorrect_signals": [],
                "primary_failure": None,
                "data_quality": "GOOD"
            }

            # Map signal keys to human readable names
            signal_map = {
                "ta_score": "Technical Analysis",
                "sentiment_score": "Sentiment Engine",
                "options_pcr": "Options Flow (PCR)",
                "options_gex": "Options Flow (GEX)",
                "vol_flow": "Volume Flow",
                "sector_rotation": "Sector Momentum",
                "insider_sentiment": "Insider Activity"
            }

            for key, val in summary.items():
                if key not in signal_map: continue
                
                # Determine if signal was bullish or bearish
                # (This is a simplification; a real system would store signal direction)
                signal_direction = "UP" if val > 0 else "DOWN" if val < 0 else "FLAT"
                
                if outcome == "RIGHT":
                    if signal_direction == direction:
                        attribution["correct_signals"].append(signal_map[key])
                    else:
                        attribution["incorrect_signals"].append(signal_map[key])
                elif outcome == "WRONG":
                    if signal_direction == direction:
                        attribution["incorrect_signals"].append(signal_map[key])
                    else:
                        attribution["correct_signals"].append(signal_map[key])

            # Check for staleness notes
            if prediction.notes and "Freshness" in prediction.notes:
                attribution["data_quality"] = "STALE"
            
            # Identify primary failure
            if outcome == "WRONG" and attribution["incorrect_signals"]:
                attribution["primary_failure"] = attribution["incorrect_signals"][0]
            
            return attribution
            
        except Exception as e:
            logger.error(f"[Postmortem] Analysis failed for Prediction #{prediction.id}: {e}")
            return {"error": str(e)}

postmortem_service = PredictionPostmortem()
