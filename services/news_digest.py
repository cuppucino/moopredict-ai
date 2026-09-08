"""
News Digest + Shadow Scorer (2026-09-08, kf-approved).

Two jobs in one evening pass, BOTH read-only w.r.t. the live prediction path:

1. WHY digest — an LLM reads each symbol's matched headlines and writes 2-3 plain
   sentences of "what's moving this and why" -> data/news_digest/YYYY-MM-DD.md +
   compact Telegram push. Purely explanatory.
2. SHADOW score — the same LLM emits a -1..+1 news score per symbol, logged NEXT TO
   the live keyword score in data/focus/shadow_news_scores.jsonl. It is NEVER used
   by any engine. Forward-only validation (deep-research rule): after the current
   experiment's t-verdict, we compare which reader predicted better, with evidence.

LLM = local Ollama llama3.1:8b (free, offline-capable); falls back to llama3.2:1b.
Deterministic engines still decide everything — the LLM only annotates.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from loguru import logger

from core.database import SessionLocal, NewsIntel

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELS = ["llama3.1:8b", "llama3.2:1b"]
LLM_TIMEOUT_S = 150  # 8b cold-load on this Mac takes >90s; warm calls ~10-20s
MAX_HEADLINES = 10

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGEST_DIR = os.path.join(ROOT, "data", "news_digest")
SHADOW_PATH = os.path.join(ROOT, "data", "focus", "shadow_news_scores.jsonl")

FOCUS_ETFS = ["SPY", "QQQ", "SMH", "XLE"]


def _matched_headlines(symbol: str, hours: int = 48) -> List[str]:
    """Same matching the live engines use — matches_ticker for stocks/ETF aliases,
    plus the focus driver terms for the 4 ETFs."""
    from services.political_monitor import matches_ticker
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    db = SessionLocal()
    try:
        rows = db.query(NewsIntel).filter(NewsIntel.scraped_at >= cutoff).all()
    finally:
        db.close()
    heads = []
    driver_pats = []
    if symbol in FOCUS_ETFS:
        try:
            from services.focus_engine import DRIVERS
            driver_pats = [re.compile(r"\b" + re.escape(t.lower()) + r"\b")
                           for t in DRIVERS.get(symbol, [])]
        except Exception:
            driver_pats = []
    for n in rows:
        text = f"{n.headline or ''} {n.summary or ''}"
        if matches_ticker(text, symbol) or any(p.search(text.lower()) for p in driver_pats):
            heads.append((n.headline or "").strip())
    return list(dict.fromkeys(h for h in heads if h))[:MAX_HEADLINES]


def _llm_read(symbol: str, headlines: List[str]) -> Optional[Dict]:
    """One LLM call: {'score': -1..1, 'why': '2-3 sentences'}. None on failure."""
    prompt = (
        "You are a financial news analyst. Below are recent headlines relevant to "
        f"{symbol}. Respond with ONLY a JSON object, no other text:\n"
        '{"score": <number -1.0 to 1.0: likely next-day price pressure from this news, '
        "negative=bearish, positive=bullish, 0=neutral/unclear>, "
        '"why": "<2-3 plain sentences: what is driving this symbol and why>"}\n\n'
        "Headlines:\n" + "\n".join(f"- {h}" for h in headlines)
    )
    for model in MODELS:
        try:
            r = requests.post(OLLAMA_URL, json={
                "model": model, "prompt": prompt, "stream": False,
                "format": "json", "options": {"temperature": 0.2},
            }, timeout=LLM_TIMEOUT_S)
            if r.status_code != 200:
                continue
            out = json.loads(r.json().get("response", "") or "{}")
            score = max(-1.0, min(1.0, float(out.get("score", 0.0))))
            why = str(out.get("why", "")).strip()
            if why:
                return {"score": round(score, 3), "why": why, "model": model}
        except Exception as e:
            logger.warning(f"[NewsDigest] {model} failed for {symbol}: {e}")
    return None


def _keyword_score(symbol: str) -> Optional[float]:
    """The LIVE reader's number, logged beside the LLM's for the comparison."""
    try:
        if symbol in FOCUS_ETFS:
            from services.focus_engine import _news_signal
            sig, _ = _news_signal(symbol)
            return round(float(sig), 3)
        from services.sentiment_engine import sentiment_engine
        s = sentiment_engine.score_symbol(symbol) or {}
        return round(float(s.get("score", 0.0) or 0.0), 3)
    except Exception:
        return None


def run_daily(push: bool = True) -> Dict:
    """Digest + shadow scores for the 4 focus ETFs + current holdings."""
    symbols = list(FOCUS_ETFS)
    try:
        from services.moomoo_service import moomoo_service
        held = [(p.get("symbol") or "").split(".")[-1].upper()
                for p in (moomoo_service.get_positions() or [])]
        symbols += [h for h in held if h and h not in symbols]
    except Exception:
        pass

    today = datetime.utcnow().strftime("%Y-%m-%d")
    lines_md = [f"# News Digest — {today}",
                "> LLM-read 'why' per symbol. Informational; the deterministic engines "
                "still make every call. Shadow scores logged for forward validation.", ""]
    tg_lines = ["🗞️ *Why digest*"]
    shadow_rows, digested = [], 0

    for sym in symbols:
        heads = _matched_headlines(sym)
        kw = _keyword_score(sym)
        if not heads:
            shadow_rows.append({"ts": datetime.utcnow().isoformat(), "symbol": sym,
                                "llm_score": None, "kw_score": kw, "n_headlines": 0})
            continue
        read = _llm_read(sym, heads)
        shadow_rows.append({"ts": datetime.utcnow().isoformat(), "symbol": sym,
                            "llm_score": (read or {}).get("score"),
                            "kw_score": kw, "n_headlines": len(heads),
                            "model": (read or {}).get("model")})
        if read:
            digested += 1
            lines_md += [f"## {sym}  (llm {read['score']:+.2f} | kw "
                         f"{kw if kw is not None else '—'})", read["why"], ""]
            lines_md += [f"- {h}" for h in heads[:5]] + [""]
            arrow = "🟢" if read["score"] > 0.15 else "🔴" if read["score"] < -0.15 else "⚪"
            tg_lines.append(f"{arrow} *{sym}*: {read['why'][:200]}")

    os.makedirs(DIGEST_DIR, exist_ok=True)
    with open(os.path.join(DIGEST_DIR, f"{today}.md"), "w") as f:
        f.write("\n".join(lines_md))
    os.makedirs(os.path.dirname(SHADOW_PATH), exist_ok=True)
    with open(SHADOW_PATH, "a") as f:
        for row in shadow_rows:
            f.write(json.dumps(row) + "\n")

    if push and digested:
        try:
            from services.notifications import notification_queue
            notification_queue.enqueue("\n".join(tg_lines[:12]), level="info",
                                       category="news_digest")
        except Exception as e:
            logger.warning(f"[NewsDigest] push failed: {e}")

    logger.info(f"[NewsDigest] {digested}/{len(symbols)} symbols digested, "
                f"{len(shadow_rows)} shadow rows logged")
    return {"success": True, "digested": digested, "symbols": len(symbols)}


news_digest = run_daily
