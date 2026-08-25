"""
Regression tests for the Data Explorer PandasAI parameter bug: direct-OpenAI
mode (BYOK / USE_OPENAI_API) with a gpt-5-family model 400'd on every
analytical call because the stock pandasai_openai wrapper always sends
`max_tokens`, which reasoning models reject (`max_completion_tokens` required).

Covers:
- create_pandasai_llm's direct-OpenAI branch builds reasoning-model requests
  without max_tokens (temperature=1.0 + reasoning_effort instead), and
  non-reasoning requests exactly as before (max_tokens/temperature/seed).
- _create_dropping_unsupported_params: the HTTP-level dynamic catch — a 400
  `unsupported_parameter` naming a param we sent is dropped (max_tokens is
  renamed to max_completion_tokens) and the call retried; anything else
  re-raises untouched.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _fake_openai_error(param, code="unsupported_parameter"):
    err = Exception(f"Error code: 400 - unsupported parameter {param}")
    err.body = {
        "message": f"Unsupported parameter: '{param}' is not supported with this model.",
        "type": "invalid_request_error",
        "param": param,
        "code": code,
    }
    return err


def _response(text="ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


# ---------------------------------------------------------------------------
# _create_dropping_unsupported_params
# ---------------------------------------------------------------------------

def test_dropper_renames_max_tokens_and_retries():
    from api_keys_config import _create_dropping_unsupported_params

    calls = []

    def create(**kwargs):
        calls.append(dict(kwargs))
        if "max_tokens" in kwargs:
            raise _fake_openai_error("max_tokens")
        return _response()

    result = _create_dropping_unsupported_params(
        create, {"model": "gpt-5.2", "max_tokens": 1000, "messages": []})

    assert result.choices[0].message.content == "ok"
    assert len(calls) == 2
    assert "max_tokens" not in calls[1]
    assert calls[1]["max_completion_tokens"] == 1000


def test_dropper_drops_other_named_params():
    from api_keys_config import _create_dropping_unsupported_params

    calls = []

    def create(**kwargs):
        calls.append(dict(kwargs))
        if "seed" in kwargs:
            raise _fake_openai_error("seed")
        return _response()

    _create_dropping_unsupported_params(
        create, {"model": "m", "seed": 99, "messages": []})
    assert len(calls) == 2
    assert "seed" not in calls[1]
    assert "max_completion_tokens" not in calls[1]  # rename is max_tokens-only


def test_dropper_reraises_unrelated_errors():
    from api_keys_config import _create_dropping_unsupported_params

    def create(**kwargs):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        _create_dropping_unsupported_params(create, {"model": "m"})


def test_dropper_reraises_when_param_not_ours():
    from api_keys_config import _create_dropping_unsupported_params

    def create(**kwargs):
        raise _fake_openai_error("max_tokens")

    # We never sent max_tokens — must not loop, must surface the error.
    with pytest.raises(Exception, match="max_tokens"):
        _create_dropping_unsupported_params(create, {"model": "m"})


def test_dropper_terminates_when_replacement_also_rejected():
    from api_keys_config import _create_dropping_unsupported_params

    calls = []

    def create(**kwargs):
        calls.append(dict(kwargs))
        if "max_tokens" in kwargs:
            raise _fake_openai_error("max_tokens")
        if "max_completion_tokens" in kwargs:
            raise _fake_openai_error("max_completion_tokens")
        return _response()

    _create_dropping_unsupported_params(
        create, {"model": "m", "max_tokens": 1000, "messages": []})
    assert len(calls) == 3
    assert "max_tokens" not in calls[2] and "max_completion_tokens" not in calls[2]


# ---------------------------------------------------------------------------
# create_pandasai_llm — direct-OpenAI branch request shape
# ---------------------------------------------------------------------------

def _make_direct_openai_llm(model_name):
    """Build the LLM from create_pandasai_llm with config forced to the
    direct-OpenAI branch and the SDK client stubbed out."""
    import api_keys_config as akc

    fake_config = {
        "api_type": "open_ai",
        "api_key": "sk-test",
        "api_base": "https://api.openai.com/v1",
        "api_version": None,
        "deployment_id": None,
        "model": model_name,
        "source": "system_openai",
        "reasoning_effort": "low" if akc._is_reasoning_model(model_name) else None,
    }
    with patch.object(akc, "get_openai_config", return_value=fake_config):
        llm = akc.create_pandasai_llm(use_alternate_api=True)
    llm.client = MagicMock()
    llm.client.create.return_value = _response("answer")
    return llm


def test_reasoning_model_request_has_no_max_tokens():
    llm = _make_direct_openai_llm("gpt-5.2")
    out = llm.chat_completion("show me employee data", None)

    assert out == "answer"
    kwargs = llm.client.create.call_args.kwargs
    assert "max_tokens" not in kwargs
    assert "seed" not in kwargs
    assert kwargs["temperature"] == 1.0
    assert "reasoning_effort" in kwargs
    assert kwargs["model"] == "gpt-5.2"
    assert kwargs["messages"][-1] == {"role": "user", "content": "show me employee data"}


def test_non_reasoning_model_keeps_classic_params():
    llm = _make_direct_openai_llm("gpt-4o")
    llm.chat_completion("hi", None)

    kwargs = llm.client.create.call_args.kwargs
    assert kwargs["max_tokens"] == llm.max_tokens
    assert "reasoning_effort" not in kwargs
    assert kwargs["model"] == "gpt-4o"


def test_reasoning_model_survives_unsupported_param_drift():
    """Even if a future reasoning model rejects a param we still send,
    the HTTP-level catch drops it and the answer comes back."""
    llm = _make_direct_openai_llm("gpt-5.2")
    calls = []

    def create(**kwargs):
        calls.append(dict(kwargs))
        if "reasoning_effort" in kwargs:
            raise _fake_openai_error("reasoning_effort")
        return _response("recovered")

    llm.client.create = create
    out = llm.chat_completion("q", None)
    assert out == "recovered"
    assert len(calls) == 2
