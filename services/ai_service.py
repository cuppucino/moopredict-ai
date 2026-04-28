import requests
import json
from loguru import logger

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
        
        prompt = (
            f"You are a market intelligence analyst for MooPredict AI.\n"
            f"Analyze the following {platform} activity and provide a very concise summary (max 3-5 bullet points).\n"
            f"Focus on market-moving news, sentiment shifts, or specific mentions of stocks/CEOs.\n\n"
            f"CONTENT:\n{formatted_content}\n\n"
            f"SUMMARY (Keep it professional and concise, use emojis):"
        )

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False
        }

        try:
            logger.info(f"[AIService] Requesting summary for {len(content_list)} items from {platform}...")
            response = requests.post(self.base_url, json=payload, timeout=120)
            if response.status_code == 200:
                result = response.json()
                summary = result.get("response", "").strip()
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
            "stream": False
        }
        try:
            logger.info(f"[AIService] Sending raw query...")
            response = requests.post(self.base_url, json=payload, timeout=120)
            if response.status_code == 200:
                return response.json().get("response", "").strip()
            return "⚠️ AI Service Error"
        except Exception as e:
            logger.error(f"[AIService] Query Exception: {e}")
            return "⚠️ AI Exception"

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
