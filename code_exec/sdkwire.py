"""
Wire the aihub_runtime SDK into a chat-lane code run.

Automations get a run token scoped to their manifest-declared names; a chat
turn has no manifest, so the chat-lane token carries the surface + user/agent
identity and a name allowlist the caller derives from what that session could
already reach through its normal tools (user parity — no new powers).
Liveness for chat tokens is the short TTL itself (execution timeout + buffer);
there is no AutomationRuns row to check. docs/code-interpreter-unification-plan.md §4.4
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Dict, Optional, Sequence

logger = logging.getLogger(__name__)


def sdk_dir() -> Optional[str]:
    """Directory containing the aihub_runtime package (PYTHONPATH-injectable).
    Source tree first, then the installed app layout — same dual resolution
    the automations runner uses."""
    src = Path(__file__).resolve().parents[1] / "automations" / "sdk"
    if (src / "aihub_runtime").is_dir():
        return str(src)
    try:
        from CommonUtils import get_app_path
        cand = get_app_path("automations", "sdk")
        if os.path.isdir(os.path.join(cand, "aihub_runtime")):
            return cand
    except Exception as e:
        logger.debug("[code_exec] sdk_dir via get_app_path failed: %s", e)
    return None


def runtime_base_url() -> Optional[str]:
    """Base URL the SDK calls back to for credential resolution."""
    override = os.getenv("AUTOMATIONS_RUNTIME_URL")
    if override:
        return override.rstrip("/")
    try:
        from CommonUtils import get_base_url
        return get_base_url().rstrip("/")
    except Exception as e:
        logger.debug("[code_exec] runtime_base_url unavailable: %s", e)
        return None


def sdk_env(surface: str,
            connections: Sequence[str] = (),
            secrets: Sequence[str] = (),
            ttl_seconds: int = 900,
            user_id=None,
            agent_id=None) -> Dict[str, str]:
    """{AIHUB_RUN_TOKEN, AIHUB_RUNTIME_URL} for one run, or {} when signing or
    the callback URL is unavailable (the SDK then fails with its own clear
    error only IF the code actually asks for a platform resource)."""
    base_url = runtime_base_url()
    if not base_url:
        return {}
    try:
        from shared_auth import sign_code_run_token
        token = sign_code_run_token(
            surface=surface,
            run_id=uuid.uuid4().hex,
            connections=list(connections or []),
            secrets=list(secrets or []),
            ttl_seconds=ttl_seconds,
            user_id=user_id,
            agent_id=agent_id,
        )
    except Exception as e:
        logger.warning("[code_exec] run-token signing unavailable: %s", e)
        return {}
    if not token:
        return {}
    return {"AIHUB_RUN_TOKEN": token, "AIHUB_RUNTIME_URL": base_url}
