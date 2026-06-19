"""
Unit tests for the Browser Use service helpers (config + runner).
=================================================================
browser_use_config / portal_runner do NOT import the heavy `browser-use` lib at
module load (it's imported lazily inside _build_llm/_build_session), so these run
in the main aihub2.1 suite. The actual ChatAnthropic/ChatOpenAI selection test
skips unless `browser-use` is importable (i.e. only runs under aihub-browseruse).

Run:
    python -m pytest tests/unit/test_browser_use_service.py -v
"""
import sys

import pytest

_SVC = r"C:/src/aihub-client-ai-dev/browser_use_service"
if _SVC not in sys.path:
    sys.path.insert(0, _SVC)

import browser_use_config as cfg   # noqa: E402
import portal_runner as runner     # noqa: E402


# --------------------------------------------------------------------------
# _provider_for_model — model id -> (provider, raw env var)
# --------------------------------------------------------------------------

def test_provider_for_model_anthropic():
    assert cfg._provider_for_model("claude-opus-4-8") == ("anthropic", "ANTHROPIC_API_KEY")
    assert cfg._provider_for_model("claude-sonnet-4-6")[0] == "anthropic"
    assert cfg._provider_for_model("anthropic.claude-x")[0] == "anthropic"


def test_provider_for_model_openai_default():
    assert cfg._provider_for_model("gpt-4o") == ("openai", "OPENAI_API_KEY")
    assert cfg._provider_for_model("")[0] == "openai"
    assert cfg._provider_for_model(None)[0] == "openai"


# --------------------------------------------------------------------------
# ensure_llm_api_key — fail-soft key resolution (pure branches)
# --------------------------------------------------------------------------

def test_ensure_llm_api_key_already_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-plaintext")
    # already present -> returns the var name without touching the encrypted value
    assert cfg.ensure_llm_api_key("claude-opus-4-8") == "ANTHROPIC_API_KEY"


def test_ensure_llm_api_key_missing_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY_ENCRYPTED", raising=False)
    assert cfg.ensure_llm_api_key("claude-opus-4-8") is None


# --------------------------------------------------------------------------
# resolve_allowed_domains — navigation allowlist (prompt-injection containment)
# --------------------------------------------------------------------------

def test_resolve_allowed_domains_restrict_on(monkeypatch):
    monkeypatch.setattr(cfg, "RESTRICT_DOMAINS", True)
    monkeypatch.setattr(cfg, "ALLOWED_DOMAINS_EXTRA", [])
    assert cfg.resolve_allowed_domains("https://portal.acme.com/login") == \
        ["*.acme.com", "portal.acme.com"]
    # bare IPs get no wildcard
    assert cfg.resolve_allowed_domains("http://127.0.0.1:8791/x") == ["127.0.0.1"]


def test_resolve_allowed_domains_with_extras(monkeypatch):
    monkeypatch.setattr(cfg, "RESTRICT_DOMAINS", True)
    monkeypatch.setattr(cfg, "ALLOWED_DOMAINS_EXTRA", ["login.microsoftonline.com"])
    out = cfg.resolve_allowed_domains("https://acme.com/")
    assert "login.microsoftonline.com" in out
    assert "acme.com" in out


def test_resolve_allowed_domains_restrict_off(monkeypatch):
    monkeypatch.setattr(cfg, "RESTRICT_DOMAINS", False)
    assert cfg.resolve_allowed_domains("https://acme.com/") is None


# --------------------------------------------------------------------------
# _build_llm — provider wrapper selection (needs browser-use installed)
# --------------------------------------------------------------------------

def test_build_llm_provider_selection():
    pytest.importorskip("browser_use")
    assert type(runner._build_llm("claude-opus-4-8")).__name__ == "ChatAnthropic"
    assert type(runner._build_llm("gpt-4o")).__name__ == "ChatOpenAI"
