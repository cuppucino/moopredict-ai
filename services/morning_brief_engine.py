"""
Morning Brief Engine — Phase 6

Generates a structured pre-market brief at 8 AM MYT every US trading day.
Scans last 24h of news + VIP tweets, scores each ETF in the universe by
mention count + sentiment + TA momentum + recency, adds a market regime
banner, and writes a markdown file to data/morning_briefs/YYYY-MM-DD.md.

Openclaw reads this brief instead of scanning from scratch every morning.
"""

from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional
import json
from loguru import logger
from sqlalchemy import desc

from core.database import SessionLocal, NewsIntel, SocialPost, Prediction
from services.political_monitor import (
    matches_ticker, _get_sentiment, TICKER_ALIASES, VIP_TIERS, get_tier
)

ETF_UNIVERSE = [
    # Broad index (1x)
    "SPY", "QQQ", "DIA", "IWM",
    # Sector SPDRs (1x)
    "XLK", "XLE", "XLF", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC",
    # Thematic (1x)
    "SOXX", "SMH", "XBI", "DRAM", "ARKK",
    # Commodity (1x)
    "GLD", "SLV", "USO",
    # ─── LEVERAGED / INVERSE (added 2026-07-02 aggressive-mode) ────────────────
    # Index 3x
    "TQQQ", "UPRO", "TNA",           # 3x bulls
    "SQQQ", "SPXU", "TZA",           # 3x bears
    # Index 2x
    "SSO", "QLD",
    # Sector 3x
    "TECL", "ERX", "FAS", "LABU",    # bulls
    "TECS", "ERY", "FAZ", "LABD",    # bears
    # Single-stock 2x — MAG 7 + memory + AMD
    "NVDL", "MUU", "TSLL", "AAPU", "MSFL", "METU", "AMZU", "GGLL", "AMDL",
    "NVDS", "MUD", "TSLQ", "AAPD", "MSFD", "METD", "AMZD", "GGLS", "AMDS",
]

# Same-underlying clusters — openclaw picks ONE per cluster per session.
# E.g., can predict QQQ UP OR TQQQ UP, not both (same bet, double-counted).
UNDERLYING_CLUSTERS = {
    "NASDAQ_100":   ["QQQ", "TQQQ", "QLD", "SQQQ"],
    "SP_500":       ["SPY", "UPRO", "SSO", "SPXU", "SPXL", "SPXS"],
    "RUSSELL_2000": ["IWM", "TNA", "TZA"],
    "SEMIS":        ["SOXX", "SMH", "SOXL", "SOXS"],
    "TECH_SECTOR":  ["XLK", "TECL", "TECS"],
    "ENERGY":       ["XLE", "ERX", "ERY"],
    "FINANCIALS":   ["XLF", "FAS", "FAZ"],
    "BIOTECH":      ["XBI", "LABU", "LABD"],
    # Single-stock clusters
    "NVDA_STOCK":   ["NVDL", "NVDS"],
    "MU_STOCK":     ["MUU", "MUD"],
    "TSLA_STOCK":   ["TSLL", "TSLQ", "TSLS"],
    "AAPL_STOCK":   ["AAPU", "AAPD"],
    "MSFT_STOCK":   ["MSFL", "MSFD"],
    "META_STOCK":   ["METU", "METD"],
    "AMZN_STOCK":   ["AMZU", "AMZD"],
    "GOOGL_STOCK":  ["GGLL", "GGLS"],
    "AMD_STOCK":    ["AMDL", "AMDS"],
    "MEMORY_ETF":   ["DRAM"],  # standalone, mentioned for completeness
}


def get_cluster_for(ticker: str) -> Optional[str]:
    """Return the cluster name a ticker belongs to, or None."""
    for cluster, members in UNDERLYING_CLUSTERS.items():
        if ticker in members:
            return cluster
    return None

# Cross-ticker inheritance: when news mentions a KEY, also credit each VALUE ETF (at 50% weight)
SECTOR_INHERITANCE = {
    # (source_stock → downstream ETFs that inherit the catalyst at 50% weight)
    "MU":     ["DRAM", "SOXX", "SMH", "MUU", "MUD"],
    "AAPL":   ["XLK", "QQQ", "AAPU", "AAPD"],
    "NVDA":   ["SOXX", "SMH", "XLK", "NVDL", "NVDS"],
    "AMD":    ["SOXX", "SMH", "AMDL", "AMDS"],
    "GOOGL":  ["XLC", "XLK", "GGLL", "GGLS"],
    "META":   ["XLC", "XLK", "METU", "METD"],
    "AMZN":   ["XLY", "XLK", "AMZU", "AMZD"],
    "TSLA":   ["XLY", "TSLL", "TSLQ", "TSLS"],
    "MSFT":   ["XLK", "MSFL", "MSFD"],
    "XOM":    ["XLE", "ERX", "ERY"],
    "CVX":    ["XLE", "ERX", "ERY"],
    "JPM":    ["XLF", "FAS", "FAZ"],
    "BAC":    ["XLF", "FAS", "FAZ"],
    "MRK":    ["XLV", "XBI", "LABU", "LABD"],
    "PFE":    ["XLV", "XBI", "LABU", "LABD"],
}


class MorningBriefEngine:
    LOG_DIR = Path("/Users/admin/moopredict-ai/data/morning_briefs")

    def generate_brief(self, target_date: Optional[date] = None) -> Dict:
        """Build brief for target_date (default: today UTC). Returns dict + writes markdown."""
        target_date = target_date or datetime.utcnow().date()
        cutoff = datetime.utcnow() - timedelta(hours=24)

        db = SessionLocal()
        try:
            news = db.query(NewsIntel).filter(NewsIntel.scraped_at >= cutoff).all()
            social = db.query(SocialPost).filter(
                SocialPost.scraped_at >= cutoff,
                SocialPost.author.isnot(None),
            ).all()
            recap = self._yesterday_recap(db, target_date)
        finally:
            db.close()

        # Filter social to VIP handles only. NOTE: VIP_TIERS is nested {tier: {handle: {...}}}
        # so `handle in VIP_TIERS` would check tier letters (S/A/B) — use get_tier() instead.
        vip_social = []
        for s in social:
            author = (s.author or "").strip()
            handle = author.lstrip("@").split()[0] if author else ""
            tier = get_tier(handle)
            if tier != "UNKNOWN":
                vip_social.append({
                    "handle": handle,
                    "tier": tier,
                    "content": s.content,
                    "posted_at": s.posted_at or s.scraped_at,
                    "sentiment": _get_sentiment(s.content or ""),
                })

        # Score each ETF
        etf_scores = {}
        for ticker in ETF_UNIVERSE:
            etf_scores[ticker] = self._score_etf(ticker, news, vip_social)

        # Regime detection (Phase 6.1)
        regime = self._detect_regime()

        # Rank top 5
        top5 = sorted(etf_scores.items(), key=lambda kv: kv[1]["score"], reverse=True)[:5]

        result = {
            "date": target_date.isoformat(),
            "generated_at": datetime.utcnow().isoformat(),
            "regime": regime,
            "top5": [{"ticker": t, **s} for t, s in top5],
            "all_scores": etf_scores,
            "yesterday_recap": recap,
            "counts": {
                "news_24h": len(news),
                "vip_social_24h": len(vip_social),
                "etfs_scanned": len(ETF_UNIVERSE),
            },
        }

        # Write markdown
        try:
            self.LOG_DIR.mkdir(parents=True, exist_ok=True)
            md = self._render_markdown(result)
            (self.LOG_DIR / f"{target_date.isoformat()}.md").write_text(md)
            result["markdown_path"] = str(self.LOG_DIR / f"{target_date.isoformat()}.md")
        except Exception as e:
            logger.error(f"[MorningBrief] Failed to write markdown: {e}")

        logger.info(f"[MorningBrief] Generated for {target_date.isoformat()}: {len(top5)} top ETFs, regime={regime['label']}")
        return result

    def _score_etf(self, ticker: str, news: list, vip_social: list) -> Dict:
        """Score one ETF. Returns dict with score, direction_lean, top_headlines, vip_tweets, notes."""
        mentions_news, bullish_n, bearish_n = 0, 0, 0
        headlines = []
        for n in news:
            text = (n.headline or "") + " " + (n.summary or "")
            # Direct match
            if matches_ticker(text, ticker):
                mentions_news += 1
                sent = _get_sentiment(text)
                if sent == "bullish":
                    bullish_n += 1
                elif sent == "bearish":
                    bearish_n += 1
                headlines.append({
                    "id": n.id,
                    "source": n.source,
                    "headline": n.headline,
                    "sentiment": sent,
                    "scraped_at": n.scraped_at.isoformat() if n.scraped_at else None,
                    "match": "direct",
                })
            else:
                # Sector inheritance (50% weight)
                for src_ticker, downstream in SECTOR_INHERITANCE.items():
                    if ticker in downstream and matches_ticker(text, src_ticker):
                        mentions_news += 0.5  # half weight
                        sent = _get_sentiment(text)
                        if sent == "bullish":
                            bullish_n += 0.5
                        elif sent == "bearish":
                            bearish_n += 0.5
                        headlines.append({
                            "id": n.id,
                            "source": n.source,
                            "headline": n.headline,
                            "sentiment": sent,
                            "scraped_at": n.scraped_at.isoformat() if n.scraped_at else None,
                            "match": f"via {src_ticker}",
                        })
                        break

        # VIP tweets
        mentions_vip, bullish_v, bearish_v = 0, 0, 0
        vip_tweets = []
        for v in vip_social:
            if matches_ticker(v["content"] or "", ticker):
                mentions_vip += 1
                if v["sentiment"] == "bullish":
                    bullish_v += 1
                elif v["sentiment"] == "bearish":
                    bearish_v += 1
                vip_tweets.append({
                    "handle": v["handle"],
                    "tier": v["tier"],
                    "content": (v["content"] or "")[:200],
                    "sentiment": v["sentiment"],
                    "posted_at": v["posted_at"].isoformat() if v["posted_at"] else None,
                })

        # Scoring
        news_mention_score = min(mentions_news * 5, 25)
        news_sent_score = max(-15, min(15, (bullish_n - bearish_n) * 5))
        vip_mention_score = min(mentions_vip * 10, 30)
        vip_sent_score = max(-15, min(15, (bullish_v - bearish_v) * 5))
        raw = news_mention_score + news_sent_score + vip_mention_score + vip_sent_score

        score = max(0, round(raw))

        # Direction lean
        net_bullish = (bullish_n + bullish_v) - (bearish_n + bearish_v)
        if net_bullish > 1:
            direction_lean = "UP"
        elif net_bullish < -1:
            direction_lean = "DOWN"
        else:
            direction_lean = "NEUTRAL"

        return {
            "score": score,
            "direction_lean": direction_lean,
            "mentions_news": round(mentions_news, 1),
            "mentions_vip": mentions_vip,
            "sentiment": {
                "bullish": round(bullish_n + bullish_v, 1),
                "bearish": round(bearish_n + bearish_v, 1),
            },
            "top_headlines": sorted(headlines, key=lambda h: h.get("scraped_at") or "", reverse=True)[:3],
            "vip_tweets": vip_tweets[:2],
        }

    def _detect_regime(self) -> Dict:
        """
        Simple regime classifier via SPY last-5d behavior.
        Returns {label, reason}. Labels: TREND_UP | TREND_DOWN | CHOP.
        """
        try:
            from services.ta_engine import ta_engine
            df = ta_engine._get_kline_data("SPY", num=6)
            if df is None or df.empty or len(df) < 5:
                return {"label": "UNKNOWN", "reason": "insufficient SPY data"}
            closes = df["close"].tail(5).tolist()
            first, last = closes[0], closes[-1]
            hi, lo = max(closes), min(closes)
            range_pct = (hi - lo) / first * 100 if first else 0
            move_pct = (last - first) / first * 100 if first else 0
            # Heuristic
            if move_pct > 1.5 and last > hi * 0.97:
                return {"label": "TREND_UP", "reason": f"SPY +{move_pct:.1f}% over 5d, near high",
                        "hint": "favour momentum/continuation setups"}
            elif move_pct < -1.5 and last < lo * 1.03:
                return {"label": "TREND_DOWN", "reason": f"SPY {move_pct:.1f}% over 5d, near low",
                        "hint": "favour cash / defensive / short setups"}
            else:
                return {"label": "CHOP", "reason": f"SPY {move_pct:+.1f}% over 5d, range {range_pct:.1f}%",
                        "hint": "favour mean reversion / RSI extremes"}
        except Exception as e:
            logger.warning(f"[MorningBrief] regime detection failed: {e}")
            return {"label": "UNKNOWN", "reason": str(e)}

    def _yesterday_recap(self, db, target_date: date) -> List[Dict]:
        """Return predictions resolved in the last 24h."""
        cutoff = datetime.combine(target_date - timedelta(days=1), datetime.min.time())
        preds = db.query(Prediction).filter(
            Prediction.resolved_at >= cutoff,
            Prediction.outcome.in_(["RIGHT", "WRONG"]),
        ).order_by(desc(Prediction.resolved_at)).limit(10).all()
        return [{
            "id": p.id,
            "symbol": p.symbol,
            "direction": p.direction,
            "outcome": p.outcome,
            "move_pct": round(p.actual_move_pct, 2) if p.actual_move_pct is not None else None,
        } for p in preds]

    def _render_markdown(self, result: Dict) -> str:
        """Render the structured dict into a copy-paste-ready markdown brief."""
        r = result
        regime = r["regime"]
        lines = [
            f"# Morning Brief — {r['date']}",
            "",
            f"> Generated {r['generated_at']}",
            f"> Regime: **{regime['label']}** — {regime.get('reason','')}",
        ]
        if regime.get("hint"):
            lines.append(f"> Hint: {regime['hint']}")
        lines += ["", "## 🎯 Top 5 ETFs by signal strength", ""]
        lines.append("| Rank | Ticker | Score | Direction lean | Mentions | Sentiment (bull/bear) |")
        lines.append("|---|---|---|---|---|---|")
        for i, e in enumerate(r["top5"], 1):
            s = e["sentiment"]
            lines.append(f"| {i} | **{e['ticker']}** | {e['score']} | {e['direction_lean']} | {e['mentions_news']}n + {e['mentions_vip']}vip | {s['bullish']}/{s['bearish']} |")

        lines += ["", "## 📰 ETF-by-ETF detail (top 5)", ""]
        for e in r["top5"]:
            lines.append(f"### {e['ticker']} (score: {e['score']})")
            lines.append(f"- Direction lean: **{e['direction_lean']}**")
            lines.append(f"- Mentions: {e['mentions_news']} news + {e['mentions_vip']} VIP tweets")
            lines.append(f"- Sentiment: {e['sentiment']['bullish']} bullish / {e['sentiment']['bearish']} bearish")
            if e["top_headlines"]:
                lines.append("- Top headlines:")
                for h in e["top_headlines"]:
                    lines.append(f"  - #{h['id']} ({h['sentiment']}, {h['match']}) [{h['source']}] {(h['headline'] or '')[:100]}")
            if e["vip_tweets"]:
                lines.append("- VIP tweets:")
                for v in e["vip_tweets"]:
                    lines.append(f"  - @{v['handle']} (T{v['tier']}, {v['sentiment']}): {v['content'][:120]}")
            lines.append("")

        lines += ["## 📊 Yesterday's resolved predictions", ""]
        if r["yesterday_recap"]:
            lines.append("| ID | Symbol | Dir | Outcome | Move% |")
            lines.append("|---|---|---|---|---|")
            for p in r["yesterday_recap"]:
                lines.append(f"| #{p['id']} | {p['symbol']} | {p['direction']} | {p['outcome']} | {p['move_pct']} |")
        else:
            lines.append("*No predictions resolved in the last 24h.*")

        lines += [
            "",
            "---",
            "",
            f"*Send to openclaw: \"Based on this brief, propose 3-5 ETF predictions (propose first, post after kf approval, timeframe=1). Regime is {regime['label']} — {regime.get('hint','')}.\"*",
            "",
            f"*Data: {r['counts']['news_24h']} news items + {r['counts']['vip_social_24h']} VIP tweets over 24h, {r['counts']['etfs_scanned']} ETFs scanned.*",
        ]
        return "\n".join(lines)


morning_brief_engine = MorningBriefEngine()
