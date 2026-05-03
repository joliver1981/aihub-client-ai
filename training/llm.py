"""Thin LLM client abstraction for training pipeline use cases.

Scope:
  - One function: complete(system, user, *, model, temperature, max_tokens).
  - Backends: "openai" (direct), "azure" (via env), "anthropic" (direct).
  - Returns assistant text only.
  - All three backends read credentials from env vars — never from the repo's
    secure_config. This module is deliberately stand-alone so training scripts
    don't drag the full AI Hub runtime (which pulls pyodbc, LangChain, etc.).

Env vars:
  OPENAI_API_KEY          openai (direct)
  OPENAI_MODEL            default model for "openai" backend
  AZURE_OPENAI_API_KEY    azure
  AZURE_OPENAI_ENDPOINT   azure
  AZURE_OPENAI_API_VERSION  (default 2024-10-21)
  AZURE_OPENAI_DEPLOYMENT default deployment for "azure" backend
  ANTHROPIC_API_KEY       anthropic
  ANTHROPIC_MODEL         default model for "anthropic" backend

Cost tracking: every call updates module-level counters so the caller can
report "N calls, M input tokens, P output tokens" at end of a batch.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("training.llm")


@dataclass
class UsageCounters:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    errors: int = 0
    seconds: float = 0.0
    per_model: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def add(self, model: str, in_toks: int, out_toks: int, seconds: float) -> None:
        self.calls += 1
        self.input_tokens += in_toks
        self.output_tokens += out_toks
        self.seconds += seconds
        pm = self.per_model.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        pm["calls"] += 1
        pm["input_tokens"] += in_toks
        pm["output_tokens"] += out_toks


USAGE = UsageCounters()


# -----------------------------------------------------------------------------
# Backends
# -----------------------------------------------------------------------------

def _complete_openai(
    system: str,
    user: str,
    model: Optional[str],
    temperature: float,
    max_tokens: int,
    response_format: Optional[Dict] = None,
) -> str:
    # Direct HTTPS call to avoid env-level openai proxy patching (the aihub2
    # conda env has openai 0.27 monkey-patched to route through an internal
    # proxy). `requests` is in every reasonable env.
    import requests  # type: ignore

    chosen = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # gpt-5 series + o1/o3/o4 reasoning models use a different schema:
    #   max_completion_tokens in place of max_tokens
    #   no temperature (or fixed at 1)
    is_reasoning_family = (
        chosen.startswith("gpt-5")
        or chosen.startswith("o1")
        or chosen.startswith("o3")
        or chosen.startswith("o4")
    )

    payload: Dict = {
        "model": chosen,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if is_reasoning_family:
        # Reasoning tokens count toward this budget — give a buffer over
        # the visible-output budget so we don't truncate.
        payload["max_completion_tokens"] = max_tokens * 3
    else:
        payload["max_tokens"] = max_tokens
        payload["temperature"] = temperature
    if response_format:
        payload["response_format"] = response_format
    t0 = time.monotonic()
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    dt = time.monotonic() - t0
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage") or {}
    USAGE.add(
        chosen,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        dt,
    )
    return data["choices"][0]["message"]["content"] or ""


def _complete_azure(
    system: str,
    user: str,
    model: Optional[str],
    temperature: float,
    max_tokens: int,
) -> str:
    from openai import AzureOpenAI  # type: ignore

    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    api_key = os.environ["AZURE_OPENAI_API_KEY"]
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    deployment = model or os.environ["AZURE_OPENAI_DEPLOYMENT"]

    client = AzureOpenAI(api_key=api_key, api_version=api_version, azure_endpoint=endpoint)
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=deployment,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    dt = time.monotonic() - t0
    USAGE.add(
        deployment,
        resp.usage.prompt_tokens if resp.usage else 0,
        resp.usage.completion_tokens if resp.usage else 0,
        dt,
    )
    return resp.choices[0].message.content or ""


def _complete_anthropic(
    system: str,
    user: str,
    model: Optional[str],
    temperature: float,
    max_tokens: int,
) -> str:
    import anthropic  # type: ignore

    client = anthropic.Anthropic()
    chosen = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    t0 = time.monotonic()
    resp = client.messages.create(
        model=chosen,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    dt = time.monotonic() - t0
    USAGE.add(chosen, resp.usage.input_tokens, resp.usage.output_tokens, dt)
    # Concatenate text blocks from response.
    parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------

def complete(
    system: str,
    user: str,
    *,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    dry_run: bool = False,
    response_format: Optional[Dict] = None,
) -> str:
    """Run a single-turn completion. Returns the assistant text.

    When dry_run is True, returns a synthetic placeholder and increments the
    `calls` counter without spending tokens. Use this in pipelines to estimate
    cost ("how many LLM calls would this script make?") before authorizing a
    real run.
    """
    backend = backend or os.getenv("TRAINING_LLM_BACKEND", "anthropic")
    if dry_run:
        USAGE.calls += 1
        return "<dry_run: no LLM called>"

    attempt = 0
    while True:
        try:
            if backend == "openai":
                return _complete_openai(system, user, model, temperature, max_tokens, response_format)
            if backend == "azure":
                return _complete_azure(system, user, model, temperature, max_tokens)
            if backend == "anthropic":
                return _complete_anthropic(system, user, model, temperature, max_tokens)
            raise ValueError(f"unknown backend: {backend}")
        except Exception as exc:  # noqa: BLE001
            attempt += 1
            if attempt >= 3:
                USAGE.errors += 1
                logger.error("LLM call failed after %d attempts: %s", attempt, exc)
                raise
            USAGE.retries += 1
            wait = 2 ** attempt
            logger.warning("LLM call failed (%s); retrying in %ds...", exc, wait)
            time.sleep(wait)


def usage_summary() -> Dict:
    return {
        "calls": USAGE.calls,
        "input_tokens": USAGE.input_tokens,
        "output_tokens": USAGE.output_tokens,
        "retries": USAGE.retries,
        "errors": USAGE.errors,
        "seconds": round(USAGE.seconds, 2),
        "per_model": USAGE.per_model,
    }
