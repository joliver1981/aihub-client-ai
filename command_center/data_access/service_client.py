"""
Command Center — Service Client
===================================
Async HTTP client for AI Hub microservices with retry and auth.
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Default retry config
MAX_RETRIES = 2
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class ServiceClient:
    """Async HTTP client for internal AI Hub service communication."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Connection": "close",
        }

    async def get(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """GET request with retry."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.get(url, headers=self._headers(), params=params)
                    if resp.status_code == 200:
                        return resp.json()
                    if resp.status_code not in RETRY_STATUS_CODES:
                        return {"error": f"HTTP {resp.status_code}", "body": resp.text[:500]}
            except httpx.TimeoutException:
                if attempt == MAX_RETRIES:
                    return {"error": f"Timeout after {self.timeout}s"}
            except Exception as e:
                if attempt == MAX_RETRIES:
                    return {"error": str(e)}

        return {"error": "Max retries exceeded"}

    async def post(self, endpoint: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        """POST request with retry."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, headers=self._headers(), json=payload or {})
                    if resp.status_code == 200:
                        return resp.json()
                    if resp.status_code not in RETRY_STATUS_CODES:
                        return {"error": f"HTTP {resp.status_code}", "body": resp.text[:500]}
            except httpx.TimeoutException:
                if attempt == MAX_RETRIES:
                    return {"error": f"Timeout after {self.timeout}s"}
            except Exception as e:
                if attempt == MAX_RETRIES:
                    return {"error": str(e)}

        return {"error": "Max retries exceeded"}
