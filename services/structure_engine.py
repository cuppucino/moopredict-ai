"""
Structure Engine — Phase 6.2

Encodes the mechanical parts of SMC/ICT-style structure analysis:
demand/supply zones, Fair Value Gaps (FVG), Break of Structure (BoS),
Fibonacci OTE band, SMA retest context, and volume Point of Control (POC).

Unlike discretionary traders who *believe* in these concepts, every prediction
gets stamped with the structure signals present at entry — so at n>=30-50 we can
MEASURE per-concept whether each signal actually lifts win rate, and delete what
doesn't (the Brendan 9,000-backtest funnel philosophy).

Pure computation. No DB writes, no scheduler. Reuses ta_engine kline fetch.
"""

from typing import Dict, List, Optional
from loguru import logger
import pandas as pd


class StructureEngine:
    PIVOT_K = 3            # fractal lookback/lookahead for swing detection
    IMPULSE_ATR_MULT = 2.0  # move must exceed this * ATR to count as an impulse
    ZONE_MAX_RANGE_PCT = 3.0  # consolidation range must be tighter than this %
    CLUSTER_PCT = 0.5     # levels within this % are clustered into one
    CONFLUENCE_PCT = 1.5  # signals within this % of price count toward confluence

    def _klines(self, symbol: str, num: int = 250) -> Optional[pd.DataFrame]:
        from services.ta_engine import ta_engine
        df = ta_engine._get_kline_data(symbol, num=num)
        if df is None or df.empty:
            return None
        return df.reset_index(drop=True)

    def _atr(self, df: pd.DataFrame, length: int = 14) -> float:
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        return float(tr.rolling(length).mean().iloc[-1]) if len(tr) >= length else float(tr.mean())

    def _find_pivots(self, df: pd.DataFrame) -> Dict[str, List[dict]]:
        """Fractal pivot highs/lows: bar is a pivot high if its high > K neighbors each side."""
        k = self.PIVOT_K
        highs, lows = [], []
        h, l = df["high"].values, df["low"].values
        for i in range(k, len(df) - k):
            if all(h[i] > h[i - j] for j in range(1, k + 1)) and all(h[i] > h[i + j] for j in range(1, k + 1)):
                highs.append({"idx": i, "price": float(h[i])})
            if all(l[i] < l[i - j] for j in range(1, k + 1)) and all(l[i] < l[i + j] for j in range(1, k + 1)):
                lows.append({"idx": i, "price": float(l[i])})
        return {"highs": highs, "lows": lows}

    def _cluster_levels(self, prices: List[float], ref_price: float) -> List[float]:
        """Merge nearby prices into levels (within CLUSTER_PCT)."""
        if not prices:
            return []
        prices = sorted(prices)
        clusters, cur = [], [prices[0]]
        for p in prices[1:]:
            if abs(p - cur[-1]) / cur[-1] * 100 <= self.CLUSTER_PCT:
                cur.append(p)
            else:
                clusters.append(sum(cur) / len(cur))
                cur = [p]
        clusters.append(sum(cur) / len(cur))
        return [round(c, 2) for c in clusters]

    def _demand_supply_zones(self, df: pd.DataFrame, atr: float) -> Dict[str, List[dict]]:
        """
        Zone = tight consolidation (<=ZONE_MAX_RANGE_PCT) immediately followed by an
        impulse move (> IMPULSE_ATR_MULT * ATR). Demand = impulse up, supply = impulse down.
        A zone is 'mitigated' if price has re-entered it after formation.
        """
        demand, supply = [], []
        window = 3        # consolidation base length
        impulse_bars = 4  # scan this many bars forward for the impulse leg
        highs, lows = df["high"].values, df["low"].values
        for i in range(window, len(df) - impulse_bars):
            base = df.iloc[i - window:i]
            base_hi, base_lo = base["high"].max(), base["low"].min()
            rng_pct = (base_hi - base_lo) / base_lo * 100 if base_lo else 999
            if rng_pct > self.ZONE_MAX_RANGE_PCT:
                continue
            # Impulse leg = cumulative move over the next `impulse_bars` bars
            leg = df.iloc[i:i + impulse_bars]
            up_move = leg["high"].max() - base_hi
            down_move = base_lo - leg["low"].min()
            if up_move > self.IMPULSE_ATR_MULT * atr:
                # rally out of the base → demand zone at the base
                mitigated = any(lows[j] <= base_hi for j in range(i + impulse_bars, len(df)))
                demand.append({"low": round(float(base_lo), 2), "high": round(float(base_hi), 2),
                               "idx": i, "mitigated": bool(mitigated)})
            elif down_move > self.IMPULSE_ATR_MULT * atr:
                mitigated = any(highs[j] >= base_lo for j in range(i + impulse_bars, len(df)))
                supply.append({"low": round(float(base_lo), 2), "high": round(float(base_hi), 2),
                               "idx": i, "mitigated": bool(mitigated)})
        # Keep most recent 5 of each, prefer unmitigated
        demand = sorted(demand, key=lambda z: (z["mitigated"], -z["idx"]))[:5]
        supply = sorted(supply, key=lambda z: (z["mitigated"], -z["idx"]))[:5]
        return {"demand": demand, "supply": supply}

    def _fvgs(self, df: pd.DataFrame) -> List[dict]:
        """3-candle FVG: bullish when candle[i-2].high < candle[i].low (gap unfilled by candle i-1)."""
        out = []
        h, l = df["high"].values, df["low"].values
        cur_price = float(df["close"].values[-1])
        for i in range(2, len(df)):
            # bullish FVG
            if h[i - 2] < l[i]:
                band = [round(float(h[i - 2]), 2), round(float(l[i]), 2)]
                filled = any(l[j] <= band[0] for j in range(i + 1, len(df)))
                out.append({"type": "bullish", "band": band, "idx": i, "filled": bool(filled)})
            # bearish FVG
            if l[i - 2] > h[i]:
                band = [round(float(h[i]), 2), round(float(l[i - 2]), 2)]
                filled = any(h[j] >= band[1] for j in range(i + 1, len(df)))
                out.append({"type": "bearish", "band": band, "idx": i, "filled": bool(filled)})
        # Most recent 6, prefer unfilled
        return sorted(out, key=lambda f: (f["filled"], -f["idx"]))[:6]

    def _fib_ote(self, pivots: Dict, cur_price: float) -> dict:
        """Last major swing (>=5% move); compute OTE band 0.62-0.79 retracement."""
        highs, lows = pivots["highs"], pivots["lows"]
        if not highs or not lows:
            return {"available": False}
        last_high = max(highs, key=lambda p: p["idx"])
        last_low = max(lows, key=lambda p: p["idx"])
        hi, lo = last_high["price"], last_low["price"]
        if hi <= lo or (hi - lo) / lo * 100 < 5:
            return {"available": False}
        # Uptrend swing (low earlier than high) → retracement measured down from high
        up = last_low["idx"] < last_high["idx"]
        span = hi - lo
        if up:
            ote_62 = hi - 0.62 * span
            ote_79 = hi - 0.79 * span
        else:
            ote_62 = lo + 0.62 * span
            ote_79 = lo + 0.79 * span
        band = sorted([round(ote_62, 2), round(ote_79, 2)])
        in_ote = band[0] <= cur_price <= band[1]
        return {"available": True, "swing_high": round(hi, 2), "swing_low": round(lo, 2),
                "direction": "up" if up else "down", "ote_band": band, "price_in_ote": bool(in_ote)}

    def _sma_context(self, df: pd.DataFrame, cur_price: float) -> dict:
        close = df["close"]
        out = {}
        for length in (50, 200):
            if len(close) >= length:
                sma = float(close.rolling(length).mean().iloc[-1])
                dist_pct = (cur_price - sma) / sma * 100
                # first retest: was >2% away 10 bars ago, now within 1%
                sma_series = close.rolling(length).mean()
                was_away = len(sma_series) > 10 and abs((close.iloc[-11] - sma_series.iloc[-11]) / sma_series.iloc[-11] * 100) > 2 if not pd.isna(sma_series.iloc[-11]) else False
                now_near = abs(dist_pct) < 1
                out[f"sma{length}"] = round(sma, 2)
                out[f"sma{length}_dist_pct"] = round(dist_pct, 2)
                out[f"first_retest_{length}"] = bool(was_away and now_near)
            else:
                out[f"sma{length}"] = None
        return out

    def _volume_poc(self, df: pd.DataFrame, bins: int = 24) -> dict:
        """Bin volume by price; POC = highest-volume price bin. HVNs = top 3."""
        lo, hi = float(df["low"].min()), float(df["high"].max())
        if hi <= lo:
            return {"poc": None, "hvns": []}
        edges = [lo + (hi - lo) * i / bins for i in range(bins + 1)]
        vol_at = [0.0] * bins
        for _, row in df.iterrows():
            mid = (row["high"] + row["low"]) / 2
            b = min(int((mid - lo) / (hi - lo) * bins), bins - 1)
            vol_at[b] += float(row["volume"])
        order = sorted(range(bins), key=lambda b: vol_at[b], reverse=True)
        def bin_mid(b): return round((edges[b] + edges[b + 1]) / 2, 2)
        return {"poc": bin_mid(order[0]), "hvns": [bin_mid(b) for b in order[:3]]}

    def _capitulation(self, df: pd.DataFrame, atr: float) -> dict:
        """
        Leverage-panic / capitulation signature (from Nicolas Meta/CapEx research, Jul 3).
        Hypothesis: forced-liquidation flushes mean-revert within 5-10 days.

        Detects on the most recent bar:
          - down move > 2x ATR (sharp drop), AND
          - volume > 1.5x the 20-bar average (panic volume)
        Returns direction + magnitude so we can later measure whether capitulation-flagged
        entries actually bounce (the thing the video guy can never verify about his own calls).
        """
        if len(df) < 21:
            return {"detected": False}
        last = df.iloc[-1]
        prev_close = float(df["close"].iloc[-2])
        move = float(last["close"]) - prev_close
        avg_vol = float(df["volume"].iloc[-21:-1].mean())
        vol_ratio = float(last["volume"]) / avg_vol if avg_vol else 0
        down_capitulation = move < -self.IMPULSE_ATR_MULT * atr and vol_ratio > 1.5
        up_blowoff = move > self.IMPULSE_ATR_MULT * atr and vol_ratio > 1.5
        if down_capitulation:
            return {"detected": True, "type": "down_capitulation",
                    "move_atr": round(abs(move) / atr, 2) if atr else None,
                    "vol_ratio": round(vol_ratio, 2),
                    "note": "sharp down + panic volume — historically bounces 5-10d"}
        if up_blowoff:
            return {"detected": True, "type": "up_blowoff",
                    "move_atr": round(abs(move) / atr, 2) if atr else None,
                    "vol_ratio": round(vol_ratio, 2),
                    "note": "sharp up + high volume — possible exhaustion top"}
        return {"detected": False}

    def _bos(self, df: pd.DataFrame, pivots: Dict) -> dict:
        """Break of structure: latest close beyond the most recent prior swing high/low."""
        closes = df["close"].values
        cur = float(closes[-1])
        highs = [p for p in pivots["highs"] if p["idx"] < len(df) - 1]
        lows = [p for p in pivots["lows"] if p["idx"] < len(df) - 1]
        last_sh = max(highs, key=lambda p: p["idx"])["price"] if highs else None
        last_sl = min(lows, key=lambda p: p["idx"])["price"] if lows else None
        if last_sh and cur > last_sh:
            return {"direction": "bullish", "level": round(last_sh, 2)}
        if last_sl and cur < last_sl:
            return {"direction": "bearish", "level": round(last_sl, 2)}
        return {"direction": "none", "level": None}

    def get_structure(self, symbol: str, num: int = 250) -> Dict:
        try:
            df = self._klines(symbol, num=num)
            if df is None or len(df) < 30:
                return {"symbol": symbol, "error": "insufficient kline data"}
            cur_price = float(df["close"].iloc[-1])
            atr = self._atr(df)
            pivots = self._find_pivots(df)
            zones = self._demand_supply_zones(df, atr)
            fvgs = self._fvgs(df)
            fib = self._fib_ote(pivots, cur_price)
            sma = self._sma_context(df, cur_price)
            vol = self._volume_poc(df)
            bos = self._bos(df, pivots)
            capitulation = self._capitulation(df, atr)

            support = self._cluster_levels([p["price"] for p in pivots["lows"]], cur_price)
            resistance = self._cluster_levels([p["price"] for p in pivots["highs"]], cur_price)

            # Confluence: count distinct signal types within CONFLUENCE_PCT of current price
            signals = []
            for z in zones["demand"]:
                if not z["mitigated"] and z["low"] * (1 - self.CONFLUENCE_PCT/100) <= cur_price <= z["high"] * (1 + self.CONFLUENCE_PCT/100):
                    signals.append("demand_zone"); break
            for z in zones["supply"]:
                if not z["mitigated"] and z["low"] * (1 - self.CONFLUENCE_PCT/100) <= cur_price <= z["high"] * (1 + self.CONFLUENCE_PCT/100):
                    signals.append("supply_zone"); break
            for f in fvgs:
                if not f["filled"] and f["band"][0] * (1 - self.CONFLUENCE_PCT/100) <= cur_price <= f["band"][1] * (1 + self.CONFLUENCE_PCT/100):
                    signals.append(f"fvg_{f['type']}"); break
            if fib.get("price_in_ote"):
                signals.append("ote_band")
            if sma.get("first_retest_50") or sma.get("first_retest_200"):
                signals.append("sma_retest")
            if vol.get("poc") and abs(vol["poc"] - cur_price) / cur_price * 100 <= self.CONFLUENCE_PCT:
                signals.append("volume_poc")
            if capitulation.get("detected"):
                signals.append(capitulation["type"])

            return {
                "symbol": symbol,
                "price": round(cur_price, 2),
                "atr": round(atr, 2),
                "levels": {"support": support[-5:], "resistance": resistance[-5:]},
                "demand_zones": zones["demand"],
                "supply_zones": zones["supply"],
                "fvgs": fvgs,
                "fib": fib,
                "sma": sma,
                "bos": bos,
                "volume_poc": vol["poc"],
                "hvns": vol["hvns"],
                "capitulation": capitulation,
                "confluence": {"score": len(signals), "signals": signals},
            }
        except Exception as e:
            logger.error(f"[StructureEngine] get_structure failed for {symbol}: {e}")
            return {"symbol": symbol, "error": str(e)}

    def confluence_at(self, symbol: str, price: float) -> Dict:
        """Count structure signals within CONFLUENCE_PCT of a given price (validate a target/stop)."""
        s = self.get_structure(symbol)
        if s.get("error"):
            return {"score": 0, "signals": [], "error": s["error"]}
        signals = []
        for z in s["demand_zones"] + s["supply_zones"]:
            if z["low"] * (1 - self.CONFLUENCE_PCT/100) <= price <= z["high"] * (1 + self.CONFLUENCE_PCT/100):
                signals.append("zone")
        for f in s["fvgs"]:
            if not f["filled"] and f["band"][0] * (1 - self.CONFLUENCE_PCT/100) <= price <= f["band"][1] * (1 + self.CONFLUENCE_PCT/100):
                signals.append(f"fvg_{f['type']}")
        for lvl in s["levels"]["support"] + s["levels"]["resistance"]:
            if abs(lvl - price) / price * 100 <= self.CONFLUENCE_PCT:
                signals.append("level")
        if s["volume_poc"] and abs(s["volume_poc"] - price) / price * 100 <= self.CONFLUENCE_PCT:
            signals.append("volume_poc")
        return {"score": len(signals), "signals": signals}


structure_engine = StructureEngine()
