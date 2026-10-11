"""Leave out `temperature` for models that reject it (2026-10-11).

Newer OpenAI models accept only their default temperature. gpt-6-luna and
gpt-6.1-sol answer any other value with HTTP 400 (code "unsupported_value",
param "temperature"). The platform sends a temperature from many places: the
quick-prompt helpers, and the LangChain clients of Command Center, the
builders and the agents. So this guards the one function they all reach, the
OpenAI library's chat-completions `create`, sync and async. Streaming is
covered too, because the rejection arrives before a stream is returned.

  * The provider names the refused parameter in a field of its own
    (error.param), so nothing here reads the error's wording.
  * Only `temperature` is ever left out, only when the caller sent it, and
    the call is retried once. Every other error is re-raised untouched.
  * The model is remembered for the life of the process, so later calls
    leave temperature out from the start instead of failing first. The model
    then runs at its default temperature, the only value it accepts.

LangChain already strips temperature for `gpt-5*` model names, which is why
the 5.6 models never needed this. It doesn't know newer names.

Kill switch: OPENAI_DROP_REJECTED_TEMPERATURE=false.
"""
import functools
import logging
import os
import threading
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger("OpenAIParamGuard")

# Parameters that may be left out when a model refuses them: a sampling knob
# whose only accepted value is the model's own default.
DROPPABLE = ("temperature",)
_REJECTION_CODES = ("unsupported_value", "unsupported_parameter")

_rejecting: dict = {}          # (endpoint, model) -> parameters that model refused
_lock = threading.Lock()


def enabled() -> bool:
    return (os.getenv("OPENAI_DROP_REJECTED_TEMPERATURE", "true") or "true").strip().lower() \
        not in ("false", "0", "no", "off")


def rejected_param(exc, kwargs: dict) -> Optional[str]:
    """The parameter the provider refused, when it is one we may leave out and
    the caller sent it; otherwise None (the error is not ours to handle)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    if not isinstance(body, dict) or body.get("code") not in _REJECTION_CODES:
        return None
    param = body.get("param")
    return param if param in DROPPABLE and param in kwargs else None


def _key(resource, kwargs: dict) -> tuple:
    """(host, model). The host, not the whole base URL: an Azure client made
    for one deployment (as LangChain makes it) carries the deployment in its
    base URL, and the same model must be recognised from any client."""
    client = getattr(resource, "_client", None)
    parts = urlsplit(str(getattr(client, "base_url", "") or ""))
    return (f"{parts.scheme}://{parts.netloc}".lower(), str(kwargs.get("model") or ""))


def known_rejections(resource, kwargs: dict) -> set:
    with _lock:
        return set(_rejecting.get(_key(resource, kwargs), ()))


def _leave_out(kwargs: dict, params) -> dict:
    return {k: v for k, v in kwargs.items() if k not in params}


def _prepare(resource, kwargs: dict) -> dict:
    drop = known_rejections(resource, kwargs) & set(kwargs)
    return _leave_out(kwargs, drop) if drop else kwargs


def _retry_kwargs(resource, exc, kwargs: dict) -> Optional[dict]:
    """kwargs for the one retry after a rejection, or None to re-raise."""
    param = rejected_param(exc, kwargs)
    if not param:
        return None
    with _lock:
        _rejecting.setdefault(_key(resource, kwargs), set()).add(param)
    logger.warning(
        "[param-guard] model %r rejected %r; leaving it out for this model from now on "
        "(retrying once). This is the model's restriction, not a caller bug.",
        kwargs.get("model"), param)
    return _leave_out(kwargs, {param})


def guard_sync(create):
    @functools.wraps(create)
    def create_leaving_out_rejected(self, *args, **kwargs):
        kwargs = _prepare(self, kwargs)
        try:
            return create(self, *args, **kwargs)
        except Exception as e:
            retry = _retry_kwargs(self, e, kwargs)
            if retry is None:
                raise
            return create(self, *args, **retry)
    create_leaving_out_rejected._aihub_param_guard = True
    return create_leaving_out_rejected


def guard_async(create):
    @functools.wraps(create)
    async def create_leaving_out_rejected(self, *args, **kwargs):
        kwargs = _prepare(self, kwargs)
        try:
            return await create(self, *args, **kwargs)
        except Exception as e:
            retry = _retry_kwargs(self, e, kwargs)
            if retry is None:
                raise
            return await create(self, *args, **retry)
    create_leaving_out_rejected._aihub_param_guard = True
    return create_leaving_out_rejected


def install() -> bool:
    """Guard the OpenAI library's chat-completions create, sync and async.
    Safe to call more than once. False when switched off or when the library
    isn't installed in this environment."""
    if not enabled():
        return False
    try:
        from openai.resources.chat import completions as cc
    except Exception as e:
        logger.debug(f"[param-guard] openai not available, not installed: {e}")
        return False
    with _lock:
        if not getattr(cc.Completions.create, "_aihub_param_guard", False):
            cc.Completions.create = guard_sync(cc.Completions.create)
        if not getattr(cc.AsyncCompletions.create, "_aihub_param_guard", False):
            cc.AsyncCompletions.create = guard_async(cc.AsyncCompletions.create)
    return True
