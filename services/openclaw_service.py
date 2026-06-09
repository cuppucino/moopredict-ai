import os
import json
from pathlib import Path
from datetime import date
import requests
from loguru import logger
from dotenv import load_dotenv

# Load env variables
load_dotenv()

class OpenClawService:
    def __init__(self):
        # Determine OpenClaw URLs
        webhook_url = os.getenv("OPENCLAW_WEBHOOK_URL", "http://127.0.0.1:18789/webhook")
        self.gateway_url = webhook_url.replace("/webhook", "").rstrip("/")
        
        # Load token
        self.token = self._resolve_token()
        if not self.token:
            raise RuntimeError("OPENCLAW_GATEWAY_TOKEN not configured via env or openclaw.json")

    def _resolve_token(self) -> str:
        # 1. Check env var
        token = os.getenv("OPENCLAW_GATEWAY_TOKEN")
        if token:
            return token.strip()
            
        # 2. Fallback: read ~/.openclaw/openclaw.json
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                    token = config.get("hooks", {}).get("token")
                    if token:
                        logger.info("Loaded OpenClaw gateway token from openclaw.json")
                        return token.strip()
            except Exception as e:
                logger.error(f"Failed to read OpenClaw gateway token from config: {e}")
                
        return ""

    def trigger_agent(self, trigger_name: str, message: str) -> bool:
        """
        Send a POST request to OpenClaw's /hooks/agent endpoint.
        Uses idempotencyKey based on trigger_name and current date to prevent duplicate firing.
        """
        url = f"{self.gateway_url}/hooks/agent"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}"
        }
        
        today_str = date.today().isoformat()
        idempotency_key = f"{trigger_name}-{today_str}"
        
        payload = {
            "message": message,
            "deliver": True,
            "idempotencyKey": idempotency_key,
            "trigger_id": idempotency_key
        }
        
        try:
            logger.info(f"[OpenClaw] Triggering agent hook at {url} | Idempotency Key: {idempotency_key}")
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code in [200, 202]:
                logger.info(f"[OpenClaw] Agent trigger succeeded: {response.status_code}")
                return True
            else:
                logger.error(f"[OpenClaw] Agent trigger failed with status {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.error(f"[OpenClaw] Error triggering agent hook: {e}")
            return False

openclaw_service = OpenClawService()
