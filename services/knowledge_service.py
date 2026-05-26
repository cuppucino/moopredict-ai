from fuzzywuzzy import fuzz
from loguru import logger
from core.database import SessionLocal, TradingKnowledge
from services.ai_service import ai_service

class KnowledgeService:
    def ingest_transcript(self, text: str) -> dict:
        """Process trading education transcripts, extract actionable rules, save to DB."""
        words = text.split()
        chunks = []
        chunk_size = 3000
        overlap = 200
        
        for i in range(0, max(1, len(words)), max(1, chunk_size - overlap)):
            chunk = " ".join(words[i:i + chunk_size])
            chunks.append(chunk)
            
        total_rules_found = 0
        total_rules_saved = 0
        all_new_rules = []
        
        for chunk in chunks:
            if not chunk.strip(): continue
            prompt = f"""
Extract actionable trading rules from the following educational transcript chunk.
Return a JSON object with a list of rules.

Format:
{{
  "rules": [
    {{
      "rule": "One sentence rule",
      "category": "ENTRY|EXIT|RISK|TIMING|CATALYST",
      "importance": 1-10
    }}
  ]
}}

Transcript:
{chunk}
"""
            res = ai_service.query_decision(prompt)
            if "error" in res or "rules" not in res:
                logger.error(f"[KnowledgeService] Failed to extract rules: {res.get('error', 'No rules field')}")
                continue
                
            rules = res.get("rules", [])
            total_rules_found += len(rules)
            
            for r in rules:
                rule_text = r.get("rule")
                if not rule_text:
                    continue
                    
                if not self._is_duplicate(rule_text):
                    saved_rule = self._save_rule(
                        rule=rule_text,
                        category=r.get("category", "GENERAL"),
                        importance=r.get("importance", 5),
                        source="transcript"
                    )
                    if saved_rule:
                        total_rules_saved += 1
                        all_new_rules.append(r)
        
        return {
            "rules_found": total_rules_found,
            "rules_saved": total_rules_saved,
            "rules": all_new_rules
        }

    def _is_duplicate(self, new_rule: str, threshold: int = 85) -> bool:
        """Check if a similar rule already exists."""
        db = SessionLocal()
        try:
            existing = db.query(TradingKnowledge).filter(TradingKnowledge.active == True).all()
            for r in existing:
                if fuzz.ratio(new_rule.lower(), r.rule.lower()) >= threshold:
                    return True
            return False
        finally:
            db.close()

    def _save_rule(self, rule: str, category: str, importance: int, source: str):
        db = SessionLocal()
        try:
            new_rule = TradingKnowledge(
                rule=rule,
                category=category,
                importance=importance,
                source=source,
                active=True
            )
            db.add(new_rule)
            db.commit()
            db.refresh(new_rule)
            return new_rule
        except Exception as e:
            logger.error(f"[KnowledgeService] Error saving rule: {e}")
            db.rollback()
            return None
        finally:
            db.close()

    def get_active_rules(self, limit: int = 15):
        db = SessionLocal()
        try:
            rules = db.query(TradingKnowledge).filter(TradingKnowledge.active == True).order_by(TradingKnowledge.importance.desc()).limit(limit).all()
            return [{"id": r.id, "rule": r.rule, "category": r.category, "importance": r.importance} for r in rules]
        finally:
            db.close()

    def get_all_rules(self):
        db = SessionLocal()
        try:
            rules = db.query(TradingKnowledge).all()
            return [{"id": r.id, "rule": r.rule, "category": r.category, "importance": r.importance, "active": r.active} for r in rules]
        finally:
            db.close()

    def deactivate_rule(self, rule_id: int):
        db = SessionLocal()
        try:
            rule = db.query(TradingKnowledge).filter(TradingKnowledge.id == rule_id).first()
            if rule:
                rule.active = False
                db.commit()
                return True
            return False
        finally:
            db.close()

knowledge_service = KnowledgeService()
