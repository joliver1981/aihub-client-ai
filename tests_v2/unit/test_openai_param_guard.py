"""openai_param_guard: leave out `temperature` for models that reject it (2026-10-11).

gpt-6-luna and gpt-6.1-sol accept only their default temperature and answer
anything else with HTTP 400 (code "unsupported_value", param "temperature").
The guard wraps the OpenAI library's chat-completions create: it retries once
without temperature when the error's own fields say so, remembers the model,
and leaves every other error alone.
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import openai_param_guard as g  # noqa: E402


class Rejected(Exception):
    def __init__(self, param="temperature", code="unsupported_value", wrapped=False):
        err = {"message": "Unsupported value", "type": "invalid_request_error", "param": param, "code": code}
        self.body = {"error": err} if wrapped else err
        super().__init__(str(self.body))


class FakeClient:
    def __init__(self, base_url="https://resource-a.example/"):
        self.base_url = base_url


class FakeCompletions:
    """Stands in for openai's Completions: rejects temperature for the models listed."""

    def __init__(self, rejects=("gpt-6-luna",), base_url="https://resource-a.example/", error=None):
        self._client = FakeClient(base_url)
        self.rejects, self.error, self.calls = set(rejects), error, []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        if kwargs.get("model") in self.rejects and "temperature" in kwargs:
            raise Rejected()
        return {"ok": True, "sent": sorted(kwargs)}


FakeCompletions.create = g.guard_sync(FakeCompletions.create)


@pytest.fixture(autouse=True)
def fresh_memory():
    g._rejecting.clear()
    yield
    g._rejecting.clear()


def test_retries_once_without_temperature_then_remembers_the_model():
    c = FakeCompletions()
    out = c.create(model="gpt-6-luna", messages=[], temperature=0.3)
    assert out["ok"] and [("temperature" in k) for k in c.calls] == [True, False]
    c.calls.clear()
    c.create(model="gpt-6-luna", messages=[], temperature=0)       # known now: no failed first try
    assert len(c.calls) == 1 and "temperature" not in c.calls[0]


def test_other_models_and_other_endpoints_keep_their_temperature():
    c = FakeCompletions()
    c.create(model="gpt-6-luna", messages=[], temperature=0.3)
    c.calls.clear()
    c.create(model="gpt-5.6-luna", messages=[], temperature=1)
    assert c.calls == [{"model": "gpt-5.6-luna", "messages": [], "temperature": 1}]
    other = FakeCompletions(rejects=(), base_url="https://resource-b.example/")
    other.create(model="gpt-6-luna", messages=[], temperature=0.3)       # same name, other resource
    assert other.calls[0]["temperature"] == 0.3


def test_the_same_model_is_recognised_from_any_client_on_that_host():
    plain = FakeCompletions(base_url="https://resource-a.example/openai/")
    plain.create(model="gpt-6-luna", messages=[], temperature=0.3)
    # LangChain's Azure client carries the deployment in its base URL
    per_deployment = FakeCompletions(base_url="https://RESOURCE-A.example/openai/deployments/gpt-6-luna/")
    per_deployment.create(model="gpt-6-luna", messages=[], temperature=0.3)
    assert len(per_deployment.calls) == 1 and "temperature" not in per_deployment.calls[0]


@pytest.mark.parametrize("error", [
    Rejected(param="messages"),                       # a different parameter
    Rejected(param="top_p"),                          # not one we leave out
    Rejected(code="content_filter"),                  # a different kind of error
    ValueError("network down"),                       # not an API error at all
])
def test_every_other_error_is_reraised_untouched(error):
    c = FakeCompletions(error=error)
    with pytest.raises(type(error)) as caught:
        c.create(model="gpt-6-luna", messages=[], temperature=0.3)
    assert caught.value is error and len(c.calls) == 1


def test_a_rejection_of_a_temperature_we_did_not_send_is_reraised():
    c = FakeCompletions(error=Rejected())
    with pytest.raises(Rejected):
        c.create(model="gpt-6-luna", messages=[])
    assert len(c.calls) == 1


def test_retries_at_most_once():
    class AlwaysRejects(FakeCompletions):
        def create(self, **kwargs):
            self.calls.append(dict(kwargs))
            raise Rejected(param="temperature") if "temperature" in kwargs else ValueError("second failure")
    AlwaysRejects.create = g.guard_sync(AlwaysRejects.create)
    c = AlwaysRejects()
    with pytest.raises(ValueError, match="second failure"):
        c.create(model="gpt-6-luna", messages=[], temperature=0.3)
    assert len(c.calls) == 2


@pytest.mark.parametrize("code,wrapped", [("unsupported_value", False), ("unsupported_parameter", False),
                                          ("unsupported_value", True)])
def test_both_rejection_codes_and_both_error_shapes(code, wrapped):
    exc = Rejected(code=code, wrapped=wrapped)
    assert g.rejected_param(exc, {"temperature": 0.3}) == "temperature"


def test_the_real_openai_error_type():
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    response = httpx.Response(400, request=httpx.Request("POST", "https://x.example/chat/completions"))
    body = {"message": "Unsupported value: 'temperature' does not support 0.3 with this model.",
            "type": "invalid_request_error", "param": "temperature", "code": "unsupported_value"}
    exc = openai.BadRequestError("Error code: 400", response=response, body=body)
    assert g.rejected_param(exc, {"model": "gpt-6-luna", "temperature": 0.3}) == "temperature"


def test_async_calls_are_guarded_too():
    class AsyncFake:
        def __init__(self):
            self._client, self.calls = FakeClient(), []

        async def create(self, **kwargs):
            self.calls.append(dict(kwargs))
            if "temperature" in kwargs:
                raise Rejected()
            return "ok"
    AsyncFake.create = g.guard_async(AsyncFake.create)
    c = AsyncFake()
    assert asyncio.run(c.create(model="gpt-6.1-sol", messages=[], temperature=0.3)) == "ok"
    assert [("temperature" in k) for k in c.calls] == [True, False]


def test_install_wraps_the_openai_library_once():
    pytest.importorskip("openai")
    from openai.resources.chat import completions as cc
    assert g.install() is True
    first = cc.Completions.create
    assert g.install() is True and cc.Completions.create is first            # not wrapped twice
    assert getattr(cc.Completions.create, "_aihub_param_guard", False)
    assert getattr(cc.AsyncCompletions.create, "_aihub_param_guard", False)


def test_switched_off(monkeypatch):
    monkeypatch.setenv("OPENAI_DROP_REJECTED_TEMPERATURE", "false")
    assert not g.enabled() and g.install() is False


def test_api_keys_config_installs_it():
    src = (ROOT / "api_keys_config.py").read_text(encoding="utf-8")
    head = src[:src.index("api_keys_bp = Blueprint(")]
    assert "import openai_param_guard" in head and "openai_param_guard.install()" in head
