"""Immutable four-hour forecast experiment for SPY, QQQ, SMH and XLE.

The module observes real prices and records paper outcomes.  It never places an order.
Range forecasts use only completed earlier sessions and are kept separate from any later
paper-trade policy so forecast quality cannot be confused with execution quality.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from loguru import logger
from sqlalchemy.exc import IntegrityError

from core.database import IntradayForecast, ForecastEvaluation, SessionLocal
from services.intraday_market import (session_window, normalize_bars, complete_window,
                                      FutuIntradayBars, market_session)
from services.notifications import enqueue_in_session
from services.paper_diagnostics import forecast_evidence


NY = ZoneInfo("America/New_York")
UTC = timezone.utc
FOCUS_SYMBOLS = ("SPY", "QQQ", "SMH", "XLE")
EXPERIMENT = "four_hour_range"
STRATEGY_VERSION = "focus-v1-range-iqr-v2"
EVALUATOR_VERSION = "four-hour-complete-v2"
EXPERIMENT_START = date(2026, 9, 23)
WINDOW_START = time(9, 30)
WINDOW_END = time(13, 30)
MIN_HISTORY_SESSIONS = 20
HISTORY_CALENDAR_DAYS = 120
REPORT_DIR = Path(__file__).resolve().parent.parent / "data" / "intraday_reports"


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)




def bars_in_window(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    bars = normalize_bars(frame)
    start, end = session_window(day)
    return bars[(bars.index >= start) & (bars.index < end)]


def excursion_history(frame: pd.DataFrame, before: Optional[date] = None) -> Dict[str, list]:
    """Four-hour maximum favourable excursions for completed historical sessions."""
    bars = normalize_bars(frame)
    samples = {"UP": [], "DOWN": []}
    if bars.empty:
        return samples
    for day_value, group in bars.groupby(bars.index.date):
        if before is not None and day_value >= before:
            continue
        try:
            window = complete_window(group, day_value)
        except ValueError:
            continue
        opening = float(window.iloc[0]["open"])
        if opening <= 0:
            continue
        samples["UP"].append((float(window["high"].max()) / opening - 1.0) * 100.0)
        samples["DOWN"].append((1.0 - float(window["low"].min()) / opening) * 100.0)
    return samples


def estimate_range(samples: Iterable[float], min_sessions: int = MIN_HISTORY_SESSIONS) -> Dict:
    """Return a robust central band from earlier four-hour excursions.

    The interquartile range is a baseline, not a claimed calibrated prediction interval.
    Both its coverage and width are reported during forward evaluation.
    """
    clean = pd.Series([float(value) for value in samples if pd.notna(value) and value >= 0])
    if len(clean) < min_sessions:
        raise ValueError(f"insufficient_history:{len(clean)}/{min_sessions}")
    low = max(0.01, float(clean.quantile(0.25)))
    high = max(low, float(clean.quantile(0.75)))
    return {
        "low_pct": round(low, 3),
        "high_pct": round(high, 3),
        "median_pct": round(float(clean.median()), 3),
        "sample_size": int(len(clean)),
        "method": "prior-session four-hour favourable-excursion IQR",
    }


def evaluate_forecast(direction: str, low_pct: float, high_pct: float,
                      frame: pd.DataFrame, day: date, require_complete: bool = False) -> Dict:
    """Score reach, magnitude and endpoint independently from completed intraday bars."""
    window = bars_in_window(frame, day)
    if window.empty:
        raise ValueError("no_bars_in_window")
    if require_complete:
        window = complete_window(frame, day)
    if not (0 <= low_pct <= high_pct < float("inf")):
        raise ValueError("invalid_range")
    opening = float(window.iloc[0]["open"])
    ending = float(window.iloc[-1]["close"])
    if opening <= 0:
        raise ValueError("invalid_open")
    raw_endpoint = (ending / opening - 1.0) * 100.0
    direction = direction.upper()
    if direction == "UP":
        excursion = (float(window["high"].max()) / opening - 1.0) * 100.0
        zone_prices = (opening * (1.0 + low_pct / 100.0),
                       opening * (1.0 + high_pct / 100.0))
        endpoint_correct = raw_endpoint > 0
    elif direction == "DOWN":
        excursion = (1.0 - float(window["low"].min()) / opening) * 100.0
        zone_prices = (opening * (1.0 - high_pct / 100.0),
                       opening * (1.0 - low_pct / 100.0))
        endpoint_correct = raw_endpoint < 0
    else:
        raise ValueError(f"unsupported_direction:{direction}")
    # Remove binary floating-point noise at exact percentage boundaries.
    excursion = round(max(0.0, excursion), 10)
    return {
        "reference_open": round(opening, 6),
        "zone_low_price": round(min(zone_prices), 6),
        "zone_high_price": round(max(zone_prices), 6),
        "actual_excursion_pct": round(excursion, 4),
        "endpoint_return_pct": round(raw_endpoint, 4),
        "target_reached": excursion >= low_pct,
        "magnitude_in_range": low_pct <= excursion <= high_pct,
        "endpoint_direction_correct": endpoint_correct,
        "evaluator_version": EVALUATOR_VERSION,
        "bar_timestamp_label": "start",
        "interval_score_50": round(high_pct - low_pct + 4 * range_error(excursion, low_pct, high_pct), 4),
        "range_error_pct": round(range_error(excursion, low_pct, high_pct), 4),
        "bars_observed": int(len(window)),
        "first_bar_at": window.index[0].isoformat(),
        "last_bar_at": window.index[-1].isoformat(),
    }


def range_error(excursion_pct: float, low_pct: float, high_pct: float) -> float:
    """Distance to the forecast band; zero means magnitude was inside the band."""
    if excursion_pct < low_pct:
        return low_pct - excursion_pct
    if excursion_pct > high_pct:
        return excursion_pct - high_pct
    return 0.0


def format_forecast_notification(result: Dict) -> str:
    """Compact Telegram-ready premarket forecast without Markdown dependencies."""
    forecasts = result.get("forecasts", [])
    lines = [f"FOUR-HOUR PAPER FORECAST — {result['session_date']}",
             "Window: 09:30–13:30 New York", "Model score is uncalibrated, not a probability of success.", ""]
    for item in forecasts:
        arrow = "↑" if item["direction"] == "UP" else "↓"
        low, high = item["move_range_pct"]
        lines.append(
            f"{item['symbol']} {arrow} {item['direction']} | model score {item['confidence']:.1f}/100 | "
            f"expected excursion {low:.2f}–{high:.2f}% | history n={item['sample_size']}"
        )
        quality = forecast_evidence(item.get("input_snapshot"))
        lines.append(f"Evidence: direct sentiment {quality['direct_sentiment']['status']}; driver news {quality['driver_news']['status']}.")
    if result.get("errors"):
        failed = ", ".join(error.get("symbol", "session") for error in result["errors"])
        lines.extend(["", f"Data unavailable: {failed}"])
    lines.extend(["", "Ranges are measured from the regular-session open.",
                  "Paper observation only — no broker orders."])
    return "\n".join(lines)


def format_resolution_notification(result: Dict) -> str:
    """Compact Telegram-ready audit with every resolved ETF visible."""
    outcomes = result.get("outcomes", [])
    reached = sum(item["target_reached"] for item in outcomes)
    in_range = sum(item["magnitude_in_range"] for item in outcomes)
    lines = [f"FOUR-HOUR PAPER AUDIT — {result['session_date']}", ""]
    for item in outcomes:
        low, high = item["expected_range_pct"]
        reach_mark = "✓ reached" if item["target_reached"] else "✗ not reached"
        range_mark = "✓ in range" if item["magnitude_in_range"] else "✗ outside range"
        lines.append(
            f"{item['symbol']} {item['direction']} | expected {low:.2f}–{high:.2f}% | "
            f"actual {item['actual_excursion_pct']:.2f}% | {reach_mark} | {range_mark}"
        )
    lines.extend(["", f"Session: {reached}/{len(outcomes)} reached; "
                         f"{in_range}/{len(outcomes)} inside range.",
                  "Paper observation only — no broker orders."])
    return "\n".join(lines)




@dataclass
class IntradayForecastService:
    bars: object = None
    session_factory: object = SessionLocal
    clock: object = lambda: datetime.now(NY)
    assessor: object = None

    def __post_init__(self):
        if self.bars is None:
            self.bars = FutuIntradayBars()

    def generate(self, day: Optional[date] = None) -> Dict:
        """Create one immutable premarket observation per ETF, idempotently."""
        now_ny = self.clock().astimezone(NY)
        day = day or now_ny.date()
        try:
            start, _ = session_window(day)
            if day != now_ny.date() or not start - timedelta(minutes=15) <= now_ny < start:
                raise ValueError("forecast_window_closed")
        except ValueError as exc:
            return {"session_date": day.isoformat(), "created": [], "existing": [],
                    "errors": [{"error": str(exc)}]}
        with self.session_factory() as db:
            found = db.query(IntradayForecast).filter_by(experiment=EXPERIMENT, session_date=day).all()
            done = {row.symbol: row.id for row in found}
        if set(FOCUS_SYMBOLS) <= set(done):
            return {"session_date": day.isoformat(), "created": [], "existing": list(done.values()),
                    "forecasts": [], "errors": []}
        assessor = self.assessor
        if assessor is None:
            from services.focus_engine import focus_engine
            assessor = lambda: focus_engine.assess(capture_sources=True)
        assessments = {item["etf"]: item for item in assessor()}

        created, existing, forecasts, errors = [], [], [], []
        for symbol in FOCUS_SYMBOLS:
            if symbol in done:
                existing.append(done[symbol])
                continue
            try:
                assessment = assessments[symbol]
                history = self.bars.fetch(
                    symbol, day - timedelta(days=HISTORY_CALENDAR_DAYS), day - timedelta(days=1)
                )
                history_samples = excursion_history(history, before=day)
                bands = {direction: estimate_range(history_samples[direction])
                         for direction in ("UP", "DOWN")}
                band = bands[assessment["direction"]]
                if self.clock().astimezone(NY).date() != day or self.clock().astimezone(NY).time() >= WINDOW_START:
                    raise ValueError("forecast_window_closed_during_data_collection")
                start_ny, end_ny = session_window(day)
                row = IntradayForecast(
                    experiment=EXPERIMENT,
                    strategy_version=STRATEGY_VERSION,
                    symbol=symbol,
                    session_date=day,
                    issued_at=_utc_naive(self.clock()),
                    window_start=_utc_naive(start_ny),
                    window_end=_utc_naive(end_ny),
                    direction=assessment["direction"],
                    confidence=assessment["confidence"],
                    move_low_pct=band["low_pct"],
                    move_high_pct=band["high_pct"],
                    sample_size=band["sample_size"],
                    input_snapshot={"focus": assessment, "range": band,
                                    "historical_bands": bands, "history_source": history.attrs.get("provenance")},
                    status="FORECASTED",
                )
                db = self.session_factory()
                try:
                    db.add(row)
                    db.flush()
                    enqueue_in_session(db, format_forecast_notification({
                        "session_date": day.isoformat(), "forecasts": [self._serialize(row)]}),
                        dedupe_key=f"forecast:{row.id}")
                    db.commit()
                    db.refresh(row)
                    created.append(row.id)
                    forecasts.append(self._serialize(row))
                except IntegrityError:
                    db.rollback()
                    found = db.query(IntradayForecast).filter_by(
                        experiment=EXPERIMENT,
                        strategy_version=STRATEGY_VERSION,
                        symbol=symbol,
                        session_date=day,
                    ).first()
                    if found:
                        existing.append(found.id)
                    else:
                        raise
                finally:
                    db.close()
            except Exception as exc:
                logger.error(f"[FourHour] forecast skipped for {symbol}: {exc}")
                errors.append({"symbol": symbol, "error": str(exc)})
        result = {"session_date": day.isoformat(), "created": created,
                  "forecasts": forecasts,
                  "existing": existing, "errors": errors}
        logger.info(f"[FourHour] {day}: created={created}, existing={existing}, errors={errors}")
        return result

    def resolve(self, day=None, *, regrade=False):
        now = self.clock().astimezone(NY)
        day = day or now.date()
        output = {"session_date": day.isoformat(), "resolved": [], "outcomes": [], "errors": []}
        try:
            _, end = session_window(day)
            if now < end:
                raise ValueError("observation_window_not_finished")
        except ValueError as exc:
            output["errors"].append({"error": str(exc)})
            return output
        with self.session_factory() as db:
            rows = db.query(IntradayForecast).filter_by(experiment=EXPERIMENT, session_date=day).all()
            records = [self._serialize(r) for r in rows if regrade or r.status == "FORECASTED"]
            done = {r.forecast_id for r in db.query(ForecastEvaluation).filter_by(
                evaluator_version=EVALUATOR_VERSION).all()}
        for record in records:
            if record["id"] in done:
                continue
            try:
                bars = self.bars.fetch(record["symbol"], day, day)
                low, high = record["move_range_pct"]
                score = evaluate_forecast(record["direction"], low, high, bars, day, True)
                band = record["input_snapshot"]["historical_bands"]["UP"]
                score["always_up_baseline"] = evaluate_forecast("UP", band["low_pct"], band["high_pct"], bars, day, True)
                score["source"] = bars.attrs.get("provenance")
                score["retrospective_regrade"] = record["status"] == "RESOLVED"
                score["original_range_model"] = record["strategy_version"]
                outcome = {"symbol": record["symbol"], "direction": record["direction"],
                           "expected_range_pct": [low, high], **score}
                with self.session_factory() as db:
                    row = db.get(IntradayForecast, record["id"])
                    db.add(ForecastEvaluation(forecast_id=row.id, evaluator_version=EVALUATOR_VERSION, result=score))
                    if row.status == "FORECASTED":
                        for field in ("reference_open", "zone_low_price", "zone_high_price", "actual_excursion_pct",
                                      "endpoint_return_pct", "target_reached", "magnitude_in_range"):
                            setattr(row, field, score[field])
                        row.result, row.status, row.resolved_at = score, "RESOLVED", _utc_naive(now)
                        enqueue_in_session(db, format_resolution_notification({**output, "outcomes": [outcome]}),
                                           dedupe_key=f"evaluation:{row.id}:{EVALUATOR_VERSION}")
                    db.commit()
                output["resolved"].append(record["id"])
                output["outcomes"].append(outcome)
            except IntegrityError:
                pass
            except Exception as exc:
                output["errors"].append({"id": record["id"], "symbol": record["symbol"], "error": str(exc)})
        output["report"] = self.render_report(day)
        return output

    def list(self, limit: int = 100) -> list:
        db = self.session_factory()
        try:
            rows = (db.query(IntradayForecast)
                    .order_by(IntradayForecast.session_date.desc(), IntradayForecast.symbol)
                    .limit(max(1, min(limit, 500))).all())
            scores = {r.forecast_id: r.result for r in db.query(ForecastEvaluation).filter(
                ForecastEvaluation.forecast_id.in_([r.id for r in rows]),
                ForecastEvaluation.evaluator_version == EVALUATOR_VERSION).all()}
            return [{**self._serialize(row), "evaluation": scores.get(row.id),
                     "legacy_score_provisional": row.strategy_version != STRATEGY_VERSION} for row in rows]
        finally:
            db.close()

    def summary(self):
        with self.session_factory() as db:
            pairs = db.query(IntradayForecast, ForecastEvaluation).join(ForecastEvaluation).filter(
                IntradayForecast.strategy_version == STRATEGY_VERSION,
                ForecastEvaluation.evaluator_version == EVALUATOR_VERSION).all()
            all_rows = db.query(IntradayForecast).filter_by(experiment=EXPERIMENT).all()
            now = self.clock().astimezone(NY)
            elapsed = []
            for stamp in pd.date_range(EXPERIMENT_START, now.date()):
                try:
                    start, _ = session_window(stamp.date())
                    if now >= start:
                        elapsed.append(stamp.date())
                except ValueError:
                    pass
            present = {(r.session_date, r.symbol) for r in all_rows}
            missing = [{"session_date": d.isoformat(), "symbols": [s for s in FOCUS_SYMBOLS if (d,s) not in present]}
                       for d in elapsed]
            output = {"experiment": EXPERIMENT, "strategy_version": STRATEGY_VERSION,
                      "evaluator_version": EVALUATOR_VERSION, "n": len(pairs),
                      "distinct_sessions": len({r.session_date for r,_ in pairs}),
                      "legacy_forecasts": sum(r.strategy_version != STRATEGY_VERSION for r in all_rows),
                      "expected_forecasts": len(elapsed)*4,
                      "missing_sessions": [r for r in missing if r["symbols"]],
                      "pending": sum(r.status == "FORECASTED" for r in all_rows)}
            if not pairs:
                return {**output, "message": "no resolved forecasts under the corrected model version yet"}
            def mean(values):
                return round(sum(values) / len(values), 4)
            scores = [e.result for _,e in pairs]
            fields = {"target_reach_rate": "target_reached", "magnitude_in_range_rate": "magnitude_in_range",
                      "endpoint_direction_rate": "endpoint_direction_correct", "mean_interval_score_50": "interval_score_50",
                      "mean_range_error_pct": "range_error_pct"}
            for label,field in fields.items():
                output[label] = mean([s[field] for s in scores])
            output["always_up_baseline"] = {label: mean([s["always_up_baseline"][field] for s in scores])
                                            for label,field in fields.items()}
            output["average_band_width_pct"] = mean([r.move_high_pct-r.move_low_pct for r,_ in pairs])
            output["brier_model_score"] = mean([(r.confidence/100-float(e.result["endpoint_direction_correct"]))**2
                                                 for r,e in pairs])
            output["score_calibration"] = "uncalibrated; observed bucket rates are descriptive, not future success probabilities"
            output["score_bands"] = []
            for low, high in ((50, 60), (60, 70), (70, 80), (80, 90), (90, 101)):
                bucket = [(r, e) for r, e in pairs if low <= r.confidence < high]
                output["score_bands"].append({"score_low_inclusive": low, "score_high_exclusive": high,
                    "n": len(bucket), "distinct_sessions": len({r.session_date for r, _ in bucket}),
                    "observed_endpoint_accuracy": mean([e.result["endpoint_direction_correct"] for _, e in bucket]) if bucket else None})
            output["by_symbol"] = {symbol: {"n": sum(r.symbol == symbol for r,_ in pairs)} for symbol in FOCUS_SYMBOLS}
            return output

    def render_report(self, day):
        rows = [r for r in self.list(500) if r["session_date"] == day.isoformat()]
        lines = [f"# Four-hour forecast audit — {day}", "", f"Evaluator: {EVALUATOR_VERSION}. Paper only.", "",
                 "| ETF | Forecast | Expected excursion | Actual excursion | Endpoint return | Status |",
                 "|---|---|---|---|---|---|"]
        for row in rows:
            score = row["evaluation"]
            low, high = row["move_range_pct"]
            status = "retrospective regrade; original band retained" if score and score["retrospective_regrade"] else row["status"]
            actual = f"{score['actual_excursion_pct']:.3f}% | {score['endpoint_return_pct']:+.3f}%" if score else "pending | pending"
            lines.append(f"| {row['symbol']} | {row['direction']} | {low:.3f}–{high:.3f}% | {actual} | {status} |")
        content = "\n".join(lines)+"\n"
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"{day}-{EVALUATOR_VERSION}.md").write_text(content)
        return content

    @staticmethod
    def _serialize(row: IntradayForecast) -> Dict:
        return {
            "id": row.id,
            "experiment": row.experiment,
            "strategy_version": row.strategy_version,
            "symbol": row.symbol,
            "session_date": row.session_date.isoformat(),
            "issued_at": row.issued_at.isoformat(),
            "window_start": row.window_start.isoformat(),
            "window_end": row.window_end.isoformat(),
            "direction": row.direction,
            "confidence": row.confidence,
            "model_score": row.confidence,
            "score_calibration": "uncalibrated; not a measured probability of success",
            "evidence_quality": forecast_evidence(row.input_snapshot),
            "move_range_pct": [row.move_low_pct, row.move_high_pct],
            "sample_size": row.sample_size,
            "status": row.status,
            "reference_open": row.reference_open,
            "price_zone": ([row.zone_low_price, row.zone_high_price]
                           if row.zone_low_price is not None else None),
            "actual_excursion_pct": row.actual_excursion_pct,
            "endpoint_return_pct": row.endpoint_return_pct,
            "target_reached": row.target_reached,
            "magnitude_in_range": row.magnitude_in_range,
            "result": row.result,
            "input_snapshot": row.input_snapshot,
        }


intraday_forecast_service = IntradayForecastService()
