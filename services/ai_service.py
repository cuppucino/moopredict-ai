import requests
import json
import time
from loguru import logger
from typing import Dict, List, Optional
from services._legacy.pattern_service import pattern_service

class AIService:
    def __init__(self, model: str = "llama3.2:1b", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = f"{base_url}/api/generate"

    def summarize_content(self, platform: str, content_list: list) -> str:
        """
        Summarize a list of posts/headlines into a concise, actionable report.
        """
        if not content_list:
            return ""

        # Cap the items to prevent context bloat and timeouts
        max_items = 15
        truncated_list = content_list[:max_items]
        
        # Format the content for the prompt
        formatted_content = "\n".join([f"- {c}" for c in truncated_list])
        
        # Fetch lessons
        lessons = pattern_service.get_confirmed_lessons()
        
        # Refined prompt to avoid "financial advice" refusals
        prompt = (
            f"You are a linguistic analysis tool for MooPredict AI. Your task is to SUMMARIZE the text provided.\n"
            f"STRICT INSTRUCTION: Adhere to the following lessons learned from past analysis:\n{lessons}\n\n"
            f"Do NOT provide financial advice, do NOT predict the market, and do NOT give recommendations.\n"
            f"Simply extract and list the key events or topics mentioned in the following {platform} data.\n"
            f"OUTPUT STRICTLY THE BULLET POINTS. DO NOT INCLUDE ANY PREAMBLE, INTRODUCTION, OR ACKNOWLEDGEMENTS.\n\n"
            f"DATA TO SUMMARIZE:\n{formatted_content}\n\n"
            f"SUMMARY (3-5 short bullet points, professional tone, use emojis):"
        )

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3
            }
        }

        # Retry logic for robustness against Ollama stalls
        max_retries = 2
        for attempt in range(max_retries):
            try:
                logger.info(f"[AIService] Requesting summary for {len(truncated_list)} items from {platform} (Attempt {attempt+1}/{max_retries})...")
                response = requests.post(self.base_url, json=payload, timeout=300)
                if response.status_code == 200:
                    result = response.json()
                    summary = result.get("response", "").strip()
                    
                    # Detect if the AI refused
                    refusal_keywords = ["as an ai", "can't assist", "can't help", "i cannot fulfill"]
                    if any(kw in summary.lower() for kw in refusal_keywords) and len(summary) < 200:
                        logger.warning(f"[AIService] AI refused to summarize {platform} content. Using fallback.")
                        return self._fallback_summary(content_list)
                        
                    return summary
                else:
                    logger.error(f"[AIService] Error from Ollama: {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"[AIService] Attempt {attempt+1} failed: {e}")
                if attempt == max_retries - 1:
                    return self._fallback_summary(content_list)
                time.sleep(2) # Small backoff before retry

        return self._fallback_summary(content_list)

    def query(self, prompt: str, symbol: Optional[str] = None) -> str:
        """Send a raw prompt to the AI and get a response."""
        lessons = pattern_service.get_confirmed_lessons(symbol)
        full_prompt = f"### CONTEXT & RULES:\n{lessons}\n\n### TASK:\n{prompt}"
        
        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": 0.4
            }
        }
        try:
            logger.info(f"[AIService] Sending raw query...")
            response = requests.post(self.base_url, json=payload, timeout=180)
            if response.status_code == 200:
                summary = response.json().get("response", "").strip()
                refusal_keywords = ["cannot provide", "as an ai", "financial advice", "can't assist", "can't help"]
                if any(kw in summary.lower() for kw in refusal_keywords):
                    return "⚠️ AI Refusal: Please rephrase or check logs."
                return summary
            return "⚠️ AI Service Error"
        except Exception as e:
            logger.error(f"[AIService] Query Exception: {e}")
            return "⚠️ AI Exception"

    def query_json(self, prompt: str) -> Dict:
        """Send a prompt to the AI and expect a JSON response with robust parsing."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1 # Lower temp for better JSON structure
            }
        }
        try:
            logger.info(f"[AIService] Sending JSON query...")
            # Increased timeout for JSON queries as they are more complex
            response = requests.post(self.base_url, json=payload, timeout=120)
            if response.status_code == 200:
                content = response.json().get("response", "").strip()
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # Fallback: Try to find JSON block in text
                    import re
                    match = re.search(r'\{.*\}', content, re.DOTALL)
                    if match:
                        try:
                            return json.loads(match.group())
                        except:
                            pass
                    logger.error(f"[AIService] Failed to parse JSON from AI: {content}")
                    return {"error": "Invalid JSON from AI"}
            return {"error": f"AI Service Error: {response.status_code}"}
        except Exception as e:
            logger.error(f"[AIService] JSON Query Exception: {e}")
            return {"error": str(e)}

    def query_decision(self, prompt: str, timeout: int = 600) -> Dict:
        """Send a complex decision prompt to glm-5.1:cloud. Returns parsed JSON."""
        payload = {
            "model": "glm-5.1:cloud",
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2}
        }
        max_retries = 2
        for attempt in range(max_retries):
            try:
                logger.info(f"[AIService] Sending decision query to glm-5.1:cloud (Attempt {attempt+1}/{max_retries})...")
                response = requests.post(self.base_url, json=payload, timeout=timeout)
                if response.status_code == 200:
                    content = response.json().get("response", "").strip()
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        import re
                        match = re.search(r'\{.*\}', content, re.DOTALL)
                        if match:
                            try:
                                return json.loads(match.group())
                            except:
                                pass
                        logger.error(f"[AIService] Failed to parse JSON from decision: {content}")
                        return {"error": "Invalid JSON from AI"}
                else:
                    logger.error(f"[AIService] Decision Error: {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"[AIService] Decision Attempt {attempt+1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
        return {"error": "Decision query failed after retries"}

    def query_lesson(self, prompt: str) -> Dict:
        """Call glm-5.1:cloud for trade lesson analysis."""
        return self.query_decision(prompt, timeout=300)

    def _fallback_summary(self, content_list: list) -> str:
        """
        Return a simple list if AI fails.
        """
        items = content_list[:5]
        summary = "⚠️ *AI Summary Unavailable (Fallback)*\n"
        summary += "\n".join([f"• {i[:100]}..." if len(i) > 100 else f"• {i}" for i in items])
        if len(content_list) > 5:
            summary += f"\n\n... and {len(content_list) - 5} more."
        return summary

# Singleton instance
ai_service = AIService()
