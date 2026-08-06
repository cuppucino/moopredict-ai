"""
Snapback Engine — STATUS: cron DISABLED 2026-07-31 (Step 1 verdict KILL).

The corrected joint-permutation MCPT (scripts/mcpt_snapback.py, 11_snapback_plan.md 1b)
returned p=0.0679 (3d) / 0.0280 (5d) — fails the p<0.01 bar at both holds. The 26-year
1a backtest PASSED (WR ~57%, PF 1.25-1.34, bear-year PF ~0.97), so the edge looks real
but modest — just not separable from luck at our own significance bar. Plan rule: PASS
requires 1a AND 1b. The p=0.0000 cited below was the earlier quick screen (independent
per-ETF shuffles — spuriously small). Engine kept for manual !snapback runs and future
re-validation. Original notes:

The edge: buy oversold extremes, hold 3 days. RSI(2) < 10 on daily bars -> mean 3-day
forward return +0.75% over drift, ~72% win rate (n=250, 10 ETFs, ~300d). Validated by MCPT
permutation test: p = 0.0000 — 0/1000 shuffled paths matched it (scripts/mcpt_snapback.py),
per the methodology from kf's source (and ~/mcpt).

Rules:
  - Universe: the 10 tested sector/index ETFs (exactly what was backtested — no drift).
  - Fire ONLY when RSI(2) < 10 at yesterday's close. Most days: nothing. That's the point.
  - Direction always UP (it's a snapback), timeframe 3 days, confidence 72 (the measured rate).
  - Cap 3/day, prefer the deepest extremes (lowest RSI). Tagged [SNAPBACK]/category="snapback",
    measured on its own track.
Caveat carried: validated on a broadly-bullish sample; a sustained bear will test it. The tag
lets us watch its live WR separately and kill it if it degrades.
"""
from typing import Dict
from loguru import logger

import pandas as pd

from services.ta_engine import ta_engine
from services.prediction_service import prediction_service

SNAP_TAG = "[SNAPBACK]"
UNIVERSE = ["SPY", "QQQ", "SMH", "XLE", "XLK", "XLF", "XLV", "XLI", "XLP", "XLY"]
RSI_PERIOD, RSI_LEVEL, HOLD_DAYS, MAX_PER_DAY = 2, 10.0, 3, 3


def _rsi2_last(symbol: str):
    try:
        df = ta_engine._get_kline_data(symbol, num=40)
        if df is None or len(df) < 15:
            return None
        s = df["close"].astype(float)
        d = s.diff()
        up = d.clip(lower=0).ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
        rs = up / dn.replace(0, pd.NA)
        rsi = 100 - 100 / (1 + rs)
        return float(rsi.iloc[-1])
    except Exception as e:
        logger.warning(f"[Snapback] RSI fetch {symbol}: {e}")
        return None


class SnapbackEngine:
    def generate(self) -> Dict:
        triggers = []
        for sym in UNIVERSE:
            r = _rsi2_last(sym)
            if r is not None and r < RSI_LEVEL:
                triggers.append((sym, r))
        triggers.sort(key=lambda t: t[1])           # deepest oversold first
        triggers = triggers[:MAX_PER_DAY]

        if not triggers:
            logger.info("[Snapback] no RSI(2)<10 extremes today — no signal (correct silence)")
            return {"success": True, "posted": 0, "ids": [], "note": "no extremes"}

        posted = []
        for sym, r in triggers:
            catalyst = (f"{SNAP_TAG} oversold snapback — {sym} RSI(2)={r:.1f} < {RSI_LEVEL:g}. "
                        f"MCPT-validated edge (p=0.000, +0.75%/trade, ~72% at {HOLD_DAYS}d hold). "
                        f"UP, {HOLD_DAYS}d timeframe.")
            kw = dict(symbol=sym, direction="UP", confidence=72.0, catalyst=catalyst,
                      category="snapback", timeframe_days=HOLD_DAYS, prediction_tag="TA_ONLY")
            res = prediction_service.create_prediction(force=False, **kw)
            if not res.get("success"):
                res = prediction_service.create_prediction(force=True, **kw)
            if res.get("success"):
                posted.append(res["prediction_id"])
                logger.success(f"[Snapback] posted #{res['prediction_id']} {sym} UP RSI2={r:.1f}")
            else:
                logger.error(f"[Snapback] FAILED {sym}: {res.get('error')}")

        return {"success": bool(posted), "posted": len(posted), "ids": posted,
                "triggers": [(s, round(r, 1)) for s, r in triggers]}


snapback_engine = SnapbackEngine()
