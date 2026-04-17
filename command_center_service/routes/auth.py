"""Command Center — Token auth endpoint"""
import logging
from fastapi import APIRouter
import httpx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])

_auth_mgr = None

def init_auth_routes(auth_mgr):
    global _auth_mgr
    _auth_mgr = auth_mgr


@router.post("/auth/validate-token")
async def validate_token(body: dict):
    """Validate a token from the main app and return user context."""
    token = body.get("token")
    if not token:
        return {"valid": False, "error": "No token"}
    try:
        from cc_config import get_base_url, AI_HUB_API_KEY
        base_url = get_base_url()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{base_url}/api/validate-cc-token",
                json={"token": token},
                headers={"X-API-Key": AI_HUB_API_KEY}
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"valid": True, "user_context": data}
            else:
                return {"valid": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
        return {"valid": False, "error": str(e)}


@router.post("/auth/refresh-token")
async def refresh_token(body: dict):
    """
    Generate a fresh CC token for a known user via server-to-server call.
    The CC service authenticates to the main app with its internal API key,
    so no Flask session is needed. This allows seamless token refresh even
    after the original token expires or the main app restarts.
    """
    user_id = body.get("user_id")
    if not user_id:
        return {"valid": False, "error": "user_id required"}
    try:
        from cc_config import get_base_url, AI_HUB_API_KEY
        base_url = get_base_url()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{base_url}/api/cc-generate-token",
                json={"user_id": user_id},
                headers={"X-API-Key": AI_HUB_API_KEY, "Content-Type": "application/json"}
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "valid": True,
                    "token": data.get("token"),
                    "user_context": data.get("user_context"),
                    "expires_in": data.get("expires_in", 14400),
                }
            else:
                return {"valid": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        return {"valid": False, "error": str(e)}
