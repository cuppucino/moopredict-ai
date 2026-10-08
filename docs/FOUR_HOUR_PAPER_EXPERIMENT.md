# Four-hour forecast experiment

SPY, QQQ, SMH and XLE receive immutable premarket UP/DOWN forecasts with positive
favorable-excursion ranges. Forecast scoring and [$100 paper-trade profitability](INTRADAY_PAPER_TRADER.md)
are separate measurements. Live broker orders remain disabled.

## Lifecycle and data

- **09:15–09:30 New York:** issue each forecast once, with retries inside this window only.
  Store direction, uncalibrated model score, prior-session range bands, input snapshot,
  source manifests and model version. Historical/future-day issuance is rejected.
- **09:30–13:30:** regular-session observation, anchored to the true opening bar's open.
- **After 13:35:** score complete observations, retry pending days, and retain errors.
  The daily paper report is queued after 13:40.

Exchange sessions come from the NYSE calendar, including DST, holidays and early closes.
Sessions closing before 13:30 are excluded. Moomoo/Futu bar timestamps mark interval
**ends**; the adapter converts once to canonical start times. Every historical range
sample and scored four-hour session must contain all 48 five-minute bars from 09:30
through 13:25. Missing, duplicate, nonfinite or invalid OHLC data is not scored.

The range is the 25th–75th percentile of earlier complete sessions' excursions in the
predicted direction, with at least 20 sessions, from up to 120 prior calendar days.
This is an unconditional historical baseline, not a calibrated 50% prediction interval.
The daily focus model still supplies direction; its daily priors/news/technicals have
not yet demonstrated four-hour trading skill. Source news, analysis, daily bar archives
and intraday bar archives are retained for audit. No historical news backfill is used.

## Scores

| Metric | Meaning |
|---|---|
| Target reached | Price moved at least to the near edge of the range |
| Magnitude in range | Maximum excursion in the predicted direction lies inside both edges |
| Endpoint direction | 13:30 price is on the predicted side of the opening price |
| Range error | Percentage-point distance from actual excursion to the nearest range edge |
| Interval score | Range width plus four times the miss distance; lower is better |
| Brier model score | Squared error of the uncalibrated directional score |

An overshoot reaches the target but fails range coverage. These metrics do not claim
that a profitable entry was available. Coverage must be assessed alongside width/error.
The scorecard compares with always-UP on the same sessions and UP excursion bands.
Missing expected ETF forecasts and pending evaluations remain visible; the sample counts
distinct sessions as well as individual ETF observations.

## Version correction on September 28, 2026

The original evaluator interpreted Futu end timestamps as starts and omitted the final
five minutes. Old forecasts and published result fields remain unchanged. Corrected
48-bar scores are appended to `forecast_evaluations` as `four-hour-complete-v2`, and
retrospective regrades are explicitly marked. Their original range bands cannot be
re-created as if correct data had been known at issuance, so those legacy observations
are excluded from the new forward scorecard.

New forecasts use `focus-v1-range-iqr-v2`. The public summary includes only that model
and evaluator pair; original and revised scores are both available in the record list.
Never combine an old published hit rate with the corrected cohort.

## Inspect

Base URL: `http://127.0.0.1:3001`.

- `GET /api/intraday-forecasts?limit=20`: immutable records, original results and revised evaluations.
- `GET /api/intraday-forecasts/summary`: corrected forward scorecard, baseline and missing sessions.
- `GET /api/intraday-forecasts/report/today`: readable per-session audit.
- `POST /api/intraday-forecasts/run`: idempotent retry, strictly inside today's premarket window.
- `POST /api/intraday-forecasts/resolve`: retry today's completed observations.

Reports are written to `data/intraday_reports/YYYY-MM-DD-four-hour-complete-v2.md`.
The durable Telegram outbox commits with each new forecast or evaluation. Historical
regrades do not resend old per-ETF result messages. The operational ledger and paper
account are described in [the paper-trader runbook](INTRADAY_PAPER_TRADER.md).
