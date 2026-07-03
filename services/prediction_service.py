from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger
from sqlalchemy.orm import Session
from core.database import SessionLocal, Prediction
from services.moomoo_service import moomoo_service

from services.validation_service import validation_service
from services.political_monitor import political_monitor
from services.news_scraper import news_scraper

def count_trading_days(start_date: datetime, end_date: datetime) -> int:
    """Count trading days (Mon-Fri) between start_date and end_date."""
    days = 0
    curr = start_date.date()
    end = end_date.date()
    while curr < end:
        if curr.weekday() < 5:
            days += 1
        curr += timedelta(days=1)
    return days


# ─── Analytics helpers (added 2026-07-03 for threshold/regime/leverage analysis) ──

# Leverage class buckets. Any symbol not listed → "1x_stock" (legacy single-stock predictions).
_LEVERAGE_MAP = {
    # 1x broad index
    **{s: "1x_index_broad" for s in ["SPY", "QQQ", "DIA", "IWM"]},
    # 1x sector SPDRs
    **{s: "1x_sector" for s in ["XLK", "XLE", "XLF", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC"]},
    # 1x thematic
    **{s: "1x_thematic" for s in ["SOXX", "SMH", "XBI", "DRAM", "ARKK"]},
    # 1x commodity
    **{s: "1x_commodity" for s in ["GLD", "SLV", "USO"]},
    # 2x index
    **{s: "2x_index" for s in ["SSO", "QLD"]},
    # 3x index (bull + bear grouped)
    **{s: "3x_index" for s in ["TQQQ", "UPRO", "TNA", "SPXL", "SQQQ", "SPXU", "TZA", "SPXS"]},
    # 3x sector (bull + bear)
    **{s: "3x_sector" for s in ["TECL", "ERX", "FAS", "LABU", "NAIL", "DPST", "TECS", "ERY", "FAZ", "LABD"]},
    # 2x single-stock (bull + bear)
    **{s: "2x_single_stock" for s in [
        "NVDL", "MUU", "TSLL", "AAPU", "MSFL", "METU", "AMZU", "GGLL", "AMDL",
        "NVDS", "MUD", "TSLQ", "TSLS", "AAPD", "MSFD", "METD", "AMZD", "GGLS", "AMDS",
    ]},
}


def classify_leverage(symbol: str) -> str:
    """Return the leverage bucket for a symbol. Defaults to '1x_stock' for legacy single-stock."""
    return _LEVERAGE_MAP.get((symbol or "").upper().strip(), "1x_stock")


def classify_regime() -> str:
    """
    Detect current market regime via SPY 5-day behavior. Same logic as morning_brief_engine
    but callable at prediction creation time (which may be off-cron).
    Returns: TREND_UP | TREND_DOWN | CHOP | UNKNOWN.
    """
    try:
        from services.ta_engine import ta_engine
        df = ta_engine._get_kline_data("SPY", num=6)
        if df is None or df.empty or len(df) < 5:
            return "UNKNOWN"
        closes = df["close"].tail(5).tolist()
        first, last = closes[0], closes[-1]
        hi, lo = max(closes), min(closes)
        move_pct = (last - first) / first * 100 if first else 0
        if move_pct > 1.5 and last > hi * 0.97:
            return "TREND_UP"
        elif move_pct < -1.5 and last < lo * 1.03:
            return "TREND_DOWN"
        else:
            return "CHOP"
    except Exception as e:
        logger.warning(f"[classify_regime] failed: {e}")
        return "UNKNOWN"


def compute_threshold_flags(direction: str, move_pct: float) -> Dict[str, bool]:
    """Return dict of would_be_right_at_XXpct booleans for the 4 threshold levels."""
    if move_pct is None or direction not in ("UP", "DOWN"):
        return {k: None for k in ("would_be_right_at_01pct", "would_be_right_at_02pct",
                                   "would_be_right_at_03pct", "would_be_right_at_05pct")}
    if direction == "UP":
        return {
            "would_be_right_at_01pct": move_pct > 0.1,
            "would_be_right_at_02pct": move_pct > 0.2,
            "would_be_right_at_03pct": move_pct > 0.3,
            "would_be_right_at_05pct": move_pct > 0.5,
        }
    else:  # DOWN
        return {
            "would_be_right_at_01pct": move_pct < -0.1,
            "would_be_right_at_02pct": move_pct < -0.2,
            "would_be_right_at_03pct": move_pct < -0.3,
            "would_be_right_at_05pct": move_pct < -0.5,
        }

def generate_thesis_from_citations(symbol: str, direction: str, citations: list) -> str:
    """Build a human-readable thesis string citing each catalyst."""
    try:
        lines = [f"{direction} on {symbol}. Catalysts:"]
        tweets = [c for c in citations if c["type"] == "tweet"]
        news = [c for c in citations if c["type"] == "news"]

        # Sort tweets by absolute weight descending, take top 3
        sorted_tweets = sorted(tweets, key=lambda c: -abs(c.get("weight", 0)))[:3]
        for t in sorted_tweets:
            sign = "+" if t.get("weight", 0) > 0 else "-"
            ts = t.get("timestamp", "")[:19]
            lines.append(
                f"  {sign} Tier-{t.get('tier')} @{t.get('handle')} ({ts}): "
                f"{t.get('content', '')[:80]}... ({t.get('sentiment')})"
            )

        # Sort news by absolute weight descending, take top 2
        sorted_news = sorted(news, key=lambda c: -abs(c.get("weight", 0)))[:2]
        for n in sorted_news:
            sign = "+" if n.get("weight", 0) > 0 else "-"
            lines.append(
                f"  {sign} News [{n.get('source')}]: {n.get('headline', '')[:80]}..."
            )

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[Prediction] Error generating thesis: {e}")
        return f"{direction} on {symbol} (Error generating citations thesis)"

class PredictionService:
    def create_prediction(
        self, 
        symbol: str, 
        direction: str, 
        confidence: float, 
        catalyst: str, 
        category: str = "general", 
        timeframe_days: int = 7,
        target_price: Optional[float] = None,
        pattern_id: Optional[int] = None,
        force: bool = False,
        prediction_tag: Optional[str] = None
    ) -> Dict:
        """
        Record a new market prediction with safety checks.
        """
        db = SessionLocal()
        try:
            symbol = symbol.upper().strip()
            direction = direction.upper().strip()
            
            # Step 1: Input Validation & Safety checks
            logger.info(f"[Prediction] step 1 started for {symbol} (Input validation)")
            warnings = []
            if not force:
                v_res = validation_service.validate_prediction(symbol, direction)
                if not v_res["passed"]:
                    logger.warning(f"[Prediction] Safety check FAILED for {symbol}: {v_res['warnings']}")
                    return {
                        "success": False, 
                        "error": "Safety check failed.", 
                        "warnings": v_res["warnings"],
                        "can_override": True
                    }
                elif v_res["warnings"]:
                    logger.info(f"[Prediction] Safety check WARNINGS for {symbol}: {v_res['warnings']}")
                warnings = v_res.get("warnings", [])
            
            # Step 2: Signal Aggregation & Consensus retrieval
            logger.info(f"[Prediction] step 2 started for {symbol} (Signal consensus)")
            from services.signal_aggregator import signal_aggregator
            consensus = signal_aggregator.get_consensus(symbol)
            auto_conf = consensus.get("confidence_score", 50)
            
            # 🛡️ Rule: Minimum 40% Confidence Threshold
            if not force and auto_conf < 40:
                logger.warning(f"[Prediction] Low confidence REJECT for {symbol}: {auto_conf}% (Min: 40%)")
                return {
                    "success": False,
                    "error": f"Confidence score too low: {auto_conf}% (Minimum 40% required)",
                    "can_override": True
                }

            # Step 3: Real-time Quote fetching
            logger.info(f"[Prediction] step 3 started for {symbol} (Fetching stock quote)")
            price_data = moomoo_service.get_stock_quote(symbol)
            entry_price = price_data.get("last_price")
            
            if entry_price is None or entry_price <= 0:
                logger.error(f"[Prediction] Failed to get entry price for {symbol}: {price_data.get('error')}")
                return {
                    "success": False,
                    "error": price_data.get("error") or "quote_timeout"
                }
            
            from services.data_freshness import freshness_registry
            freshness_snapshot = freshness_registry.stamp_prediction_context(symbol)
            
            # Step 4: Scraping recent tweets and news headlines (catalyst data)
            logger.info(f"[Prediction] step 4 started for {symbol} (Fetching catalyst data)")
            tweets = political_monitor.get_recent_ticker_tweets(symbol, hours=24)
            news = news_scraper.get_recent_for_ticker(symbol, hours=24)
            
            # We will compute the tag after citations are built in Step 5
            
            # Step 5: Citation assembly & final confidence calculation
            logger.info(f"[Prediction] step 5 started for {symbol} (Citations and thesis assembly)")
            confidence_delta = 0.0
            citations = []
            
            # VIP Tweets modifier
            for tweet in tweets:
                tier = tweet.get("tier")
                sent = tweet.get("sentiment")
                weight = {"S": 0.20, "A": 0.10, "B": 0.05}.get(tier, 0.0)
                
                if sent == "neutral":
                    signed_weight = 0.0
                else:
                    align = (sent == "bullish" and direction == "UP") or (sent == "bearish" and direction == "DOWN")
                    signed_weight = weight if align else -weight
                
                confidence_delta += signed_weight * 100
                citations.append({
                    "type": "tweet",
                    "handle": tweet.get("handle"),
                    "tier": tier,
                    "timestamp": tweet.get("timestamp"),
                    "content": tweet.get("content"),
                    "sentiment": sent,
                    "weight": signed_weight
                })
                
            # News Headlines modifier
            for headline in news:
                sent = headline.get("sentiment")
                if sent == "neutral":
                    signed_weight = 0.0
                else:
                    align = (sent == "bullish" and direction == "UP") or (sent == "bearish" and direction == "DOWN")
                    signed_weight = 0.05 if align else -0.05
                    
                confidence_delta += signed_weight * 100
                citations.append({
                    "type": "news",
                    "source": headline.get("source"),
                    "timestamp": headline.get("timestamp"),
                    "headline": headline.get("headline"),
                    "sentiment": sent,
                    "weight": signed_weight
                })
                
            # No catalyst penalty
            if not tweets and not news:
                confidence_delta -= 5.0
                
            # Apply bounds
            final_confidence = max(20.0, min(95.0, confidence + confidence_delta))
            
            # Build cited thesis. Preserve user-provided catalyst as the lead line;
            # auto-generated citation lines follow underneath.
            user_catalyst = (catalyst or "").strip()
            if citations:
                auto = generate_thesis_from_citations(symbol, direction, citations)
                thesis = f"{user_catalyst}\n\n{auto}" if user_catalyst else auto
            else:
                thesis = user_catalyst or f"{direction} on {symbol} from TA composite only (no catalysts in 24h)"
            
            # Compute tag if not overridden or if overridden with an invalid value
            valid_tags = {"CATALYST_DRIVEN", "WEAK_CATALYST", "TA_ONLY", "INFERRED_CATALYST"}
            if prediction_tag not in valid_tags:
                prediction_tag = None

            if not prediction_tag:
                if any(abs(c.get("weight", 0)) >= 0.10 for c in citations):
                    prediction_tag = "CATALYST_DRIVEN"
                elif citations:
                    prediction_tag = "WEAK_CATALYST"
                else:
                    prediction_tag = "TA_ONLY"

            # Step 5.5: analytics stamping — regime + leverage class at time of creation
            regime_at_open = classify_regime()
            leverage_class = classify_leverage(symbol)

            # Step 6: Database transaction (inserting the Prediction object)
            logger.info(f"[Prediction] step 6 started for {symbol} (Writing prediction to database)")
            prediction = Prediction(
                symbol=symbol,
                prediction_tag=prediction_tag,
                direction=direction,
                confidence=final_confidence,
                confidence_score=auto_conf,
                signal_summary=consensus,
                catalyst=thesis,
                category=category,
                timeframe_days=timeframe_days,
                entry_price=entry_price,
                target_price=target_price,
                pattern_id=pattern_id,
                deadline=datetime.utcnow() + timedelta(days=timeframe_days),
                created_at=datetime.utcnow(),
                notes=f"Freshness: {freshness_snapshot}",
                thesis_citations=citations,
                regime_at_open=regime_at_open,
                leverage_class=leverage_class,
            )
            
            db.add(prediction)
            db.commit()
            db.refresh(prediction)
            
            logger.info(f"[Prediction] Recorded #{prediction.id} for {symbol} ({direction})")
            
            return {
                "success": True, 
                "prediction_id": prediction.id, 
                "entry_price": entry_price,
                "warnings": warnings
            }
            
        except Exception as e:
            logger.error(f"[Prediction] Create error: {e}")
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def resolve_pending_predictions(self) -> List[Dict]:
        """
        Check all pending predictions, resolve those past their deadline,
        and auto-expire those exceeding the 10-trading-day limit to NEUTRAL.
        """
        db = SessionLocal()
        resolved_list = []
        try:
            now = datetime.utcnow()
            pending = db.query(Prediction).filter(Prediction.outcome == None).all()
            
            for pred in pending:
                if not pred.created_at:
                    logger.critical(f"[Prediction] Data integrity issue: Prediction #{pred.id} has no created_at. Treating as ancient (1970-01-01).")
                    created_at = datetime(1970, 1, 1)
                else:
                    created_at = pred.created_at
                trading_days = count_trading_days(created_at, now)
                if trading_days >= 10:
                    # Auto-expire to NEUTRAL
                    pred.outcome = "NEUTRAL"
                    pred.exit_price = pred.entry_price
                    pred.actual_move_pct = 0.0
                    pred.resolved_at = now
                    pred.notes = f"Auto-expired to NEUTRAL after exceeding 10-trading-day maximum hold period (actual: {trading_days} trading days)."
                    db.commit()
                    logger.info(f"[Prediction] Auto-expired #{pred.id} to NEUTRAL after {trading_days} trading days.")
                    resolved_list.append({
                        "success": True,
                        "id": pred.id,
                        "symbol": pred.symbol,
                        "outcome": "NEUTRAL",
                        "move_pct": 0.0
                    })
                elif pred.deadline <= now:
                    result = self.resolve_prediction(pred.id)
                    if result.get("success"):
                        resolved_list.append(result)
            
            return resolved_list
        except Exception as e:
            logger.error(f"[Prediction] Auto-resolve error: {e}")
            return []
        finally:
            db.close()

    def resolve_prediction(self, prediction_id: int, manual_notes: str = "") -> Dict:
        """
        Evaluate a single prediction against current market data.
        """
        db = SessionLocal()
        try:
            prediction = db.query(Prediction).filter(Prediction.id == prediction_id).first()
            if not prediction:
                return {"success": False, "error": "Prediction not found"}
            
            if prediction.outcome:
                return {"success": False, "error": "Already resolved"}
            
            # Check for 10-trading-day auto-expire to NEUTRAL
            if not prediction.created_at:
                logger.critical(f"[Prediction] Data integrity issue: Prediction #{prediction.id} has no created_at. Treating as ancient (1970-01-01).")
                created_at = datetime(1970, 1, 1)
            else:
                created_at = prediction.created_at
            trading_days = count_trading_days(created_at, datetime.utcnow())
            if trading_days >= 10:
                prediction.outcome = "NEUTRAL"
                prediction.exit_price = prediction.entry_price
                prediction.actual_move_pct = 0.0
                prediction.resolved_at = datetime.utcnow()
                prediction.notes = f"Auto-expired to NEUTRAL after exceeding 10-trading-day maximum hold period (actual: {trading_days} trading days). {manual_notes}".strip()
                db.commit()
                logger.info(f"[Prediction] Auto-expired #{prediction.id} to NEUTRAL after {trading_days} trading days.")
                return {
                    "success": True,
                    "id": prediction.id,
                    "symbol": prediction.symbol,
                    "outcome": "NEUTRAL",
                    "move_pct": 0.0
                }

            # Get current price
            price_data = moomoo_service.get_stock_quote(prediction.symbol)
            exit_price = price_data.get("last_price", 0.0)
            
            if exit_price <= 0:
                return {"success": False, "error": "Could not fetch exit price"}

            move_pct = ((exit_price - prediction.entry_price) / prediction.entry_price) * 100 if prediction.entry_price > 0 else 0
            
            outcome = "WRONG"
            if prediction.direction == "UP" and move_pct > 0.5: # 0.5% buffer
                outcome = "RIGHT"
            elif prediction.direction == "DOWN" and move_pct < -0.5:
                outcome = "RIGHT"
            elif prediction.direction == "FLAT" and abs(move_pct) <= 0.5:
                outcome = "RIGHT"
            
            prediction.outcome = outcome
            prediction.exit_price = exit_price
            prediction.actual_move_pct = move_pct
            prediction.resolved_at = datetime.utcnow()
            prediction.notes = manual_notes

            # Analytics: stamp threshold-would-be-right flags for 0.1/0.2/0.3/0.5%
            flags = compute_threshold_flags(prediction.direction, move_pct)
            for k, v in flags.items():
                setattr(prediction, k, v)
            
            # --- Auto Post-Mortem ---
            try:
                from services.prediction_postmortem import postmortem_service
                prediction.postmortem = postmortem_service.analyze(prediction)
            except Exception as pe:
                logger.error(f"[Prediction] Postmortem failed: {pe}")
            
            db.commit()
            logger.info(f"[Prediction] Resolved #{prediction.id} as {outcome} ({move_pct:.2f}%)")
            
            return {
                "success": True, 
                "id": prediction.id, 
                "symbol": prediction.symbol, 
                "outcome": outcome, 
                "move_pct": move_pct
            }
            
        except Exception as e:
            logger.error(f"[Prediction] Resolve error: {e}")
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def get_stats(self) -> Dict:
        """
        Calculate accuracy statistics.
        """
        db = SessionLocal()
        try:
            total = db.query(Prediction).filter(Prediction.outcome != None).count()
            right = db.query(Prediction).filter(Prediction.outcome == "RIGHT").count()
            
            accuracy = (right / total * 100) if total > 0 else 0
            
            # By category
            categories = db.query(Prediction.category).distinct().all()
            cat_stats = {}
            for (cat,) in categories:
                c_total = db.query(Prediction).filter(Prediction.category == cat, Prediction.outcome != None).count()
                if c_total > 0:
                    c_right = db.query(Prediction).filter(Prediction.category == cat, Prediction.outcome == "RIGHT").count()
                    cat_stats[cat] = round((c_right / c_total * 100), 2)

            return {
                "total_resolved": total,
                "accuracy_pct": round(accuracy, 2),
                "by_category": cat_stats
            }
        except Exception as e:
            logger.error(f"[Prediction] Stats error: {e}")
            return {}
        finally:
            db.close()

    def get_active(self, limit: int = 10) -> List[Dict]:
        db = SessionLocal()
        try:
            preds = db.query(Prediction).filter(Prediction.outcome == None).order_by(Prediction.deadline.asc()).limit(limit).all()
            return [
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "confidence": p.confidence,
                    "deadline": p.deadline.isoformat(),
                    "catalyst": p.catalyst,
                    "thesis_citations": p.thesis_citations or []
                } for p in preds
            ]
        finally:
            db.close()

    def get_all(self, limit: int = 50, resolved_only: bool = False) -> List[Dict]:
        db = SessionLocal()
        try:
            q = db.query(Prediction)
            if resolved_only:
                q = q.filter(Prediction.outcome.in_(["RIGHT", "WRONG"]))
            preds = q.order_by(Prediction.id.desc()).limit(limit).all()
            return [
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "confidence": p.confidence,
                    "catalyst": p.catalyst,
                    "category": p.category,
                    "prediction_tag": p.prediction_tag,
                    "outcome": p.outcome,
                    "entry_price": p.entry_price,
                    "target_price": p.target_price,
                    "exit_price": p.exit_price,
                    "actual_move_pct": p.actual_move_pct,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "deadline": p.deadline.isoformat() if p.deadline else None,
                    "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
                    "thesis_citations": p.thesis_citations or []
                } for p in preds
            ]
        finally:
            db.close()

prediction_service = PredictionService()
