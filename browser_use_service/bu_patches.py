"""
bu_patches.py — runtime hardening patches for the pinned browser-use 0.12.9.

Imported for its side effects at Browser Use service startup (main.py). Each patch is
fail-soft and a strict SUPERSET of the original behavior — it can only rescue calls that
would otherwise raise, never change a call that already succeeds.

PATCH 1 — tolerant structured-output JSON parsing.
  browser-use parses the model's structured output with `AgentOutput.model_validate_json(
  choice.message.content)` (browser_use/llm/openai/chat.py). Reasoning models on Azure
  (gpt-5.x) intermittently append trailing characters AFTER an otherwise-valid JSON object
  (observed: `{...valid...}\n]}`), which pydantic's strict JSON reader rejects with
  "Invalid JSON: trailing characters", so ~half the agent steps produce no action and the
  run burns its step budget without finishing (portal downloads never complete). We make
  `BaseModel.model_validate_json` fall back to `json.JSONDecoder().raw_decode`, which parses
  the FIRST complete JSON value and ignores trailing junk (and strips a ```json fence if
  present), then validates the resulting object. Valid input is unaffected (the original is
  tried first); only otherwise-failing input is rescued.
"""
import json as _json
import logging

log = logging.getLogger("browser_use_service")


def _install_tolerant_json():
    try:
        from pydantic import BaseModel
    except Exception as e:  # pragma: no cover
        log.warning("bu_patches: pydantic not importable, skipping tolerant-JSON patch: %s", e)
        return

    if getattr(BaseModel, "_aihub_tolerant_json", False):
        return  # idempotent

    _orig = BaseModel.__dict__["model_validate_json"].__func__  # unwrap the classmethod

    def _extract_first_json(text):
        """Return the first complete JSON value in `text`, ignoring leading fences and any
        trailing characters. Raises ValueError if none found."""
        s = text.decode("utf-8", "replace") if isinstance(text, (bytes, bytearray)) else str(text)
        s = s.strip()
        if s.startswith("```"):
            s = s[3:]
            if s[:4].lower() == "json":
                s = s[4:]
            fence = s.rfind("```")
            if fence != -1:
                s = s[:fence]
            s = s.strip()
        # raw_decode parses the first JSON value and returns where it ended; trailing junk
        # after that index is discarded.
        start = s.find("{")
        if start > 0:
            s = s[start:]
        obj, _end = _json.JSONDecoder().raw_decode(s)
        return obj

    def _tolerant_model_validate_json(cls, json_data, *args, **kwargs):
        try:
            return _orig(cls, json_data, *args, **kwargs)
        except Exception as first_err:
            try:
                obj = _extract_first_json(json_data)
            except Exception:
                raise first_err  # not recoverable — surface the original error
            # model_validate takes python objects (drop json-only kwargs like strict/context
            # defensively; model_validate accepts strict/context too, so pass them through).
            result = cls.model_validate(obj, **kwargs)
            log.debug("bu_patches: rescued a trailing-characters JSON parse for %s", getattr(cls, "__name__", cls))
            return result

    BaseModel.model_validate_json = classmethod(_tolerant_model_validate_json)
    BaseModel._aihub_tolerant_json = True
    log.info("bu_patches: tolerant structured-output JSON parsing installed (browser-use hardening)")


def install_all():
    _install_tolerant_json()
