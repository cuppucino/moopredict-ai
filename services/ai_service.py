import requests
import json
from loguru import logger
from typing import Dict, List, Optional

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

        # Format the content for the prompt
        formatted_content = "\n".join([f"- {c}" for c in content_list])
        
        # Refined prompt to avoid "financial advice" refusals
        prompt = (
            f"You are a linguistic analysis tool for MooPredict AI. Your task is to SUMMARIZE the text provided.\n"
            f"Do NOT provide financial advice, do NOT predict the market, and do NOT give recommendations.\n"
            f"Simply extract and list the key events or topics mentioned in the following {platform} data.\n\n"
            f"DATA TO SUMMARIZE:\n{formatted_content}\n\n"
            f"SUMMARY (3-5 short bullet points, professional tone, use emojis):"
        )

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3 # Lower temperature for more factual summaries
            }
        }

        try:
            logger.info(f"[AIService] Requesting summary for {len(content_list)} items from {platform}...")
            response = requests.post(self.base_url, json=payload, timeout=120)
            if response.status_code == 200:
                result = response.json()
                summary = result.get("response", "").strip()
                
                # Detect if the AI refused (common in llama models)
                refusal_keywords = ["cannot provide", "as an ai", "financial advice", "legal advice", "predict the stock", "can't assist", "can't help"]
                if any(kw in summary.lower() for kw in refusal_keywords):
                    logger.warning(f"[AIService] AI refused to summarize {platform} content. Using fallback.")
                    return self._fallback_summary(content_list)
                    
                return summary
            else:
                logger.error(f"[AIService] Error from Ollama: {response.status_code} - {response.text}")
                return self._fallback_summary(content_list)
        except Exception as e:
            logger.error(f"[AIService] Exception during AI request: {e}")
            return self._fallback_summary(content_list)

    def query(self, prompt: str) -> str:
        """Send a raw prompt to the AI and get a response."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.4
            }
        }
        try:
            logger.info(f"[AIService] Sending raw query...")
            response = requests.post(self.base_url, json=payload, timeout=120)
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
        """Send a prompt to the AI and expect a JSON response."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.2
            }
        }
        try:
            logger.info(f"[AIService] Sending JSON query...")
            response = requests.post(self.base_url, json=payload, timeout=120)
            if response.status_code == 200:
                content = response.json().get("response", "").strip()
                return json.loads(content)
            return {"error": "AI Service Error"}
        except Exception as e:
            logger.error(f"[AIService] JSON Query Exception: {e}")
            return {"error": str(e)}

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
