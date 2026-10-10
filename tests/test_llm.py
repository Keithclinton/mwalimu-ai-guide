import json
from types import SimpleNamespace

import anthropic
import httpx2 as httpx  # anthropic 1.x is built on httpx2; plain httpx objects are rejected
import pytest
from fastapi.testclient import TestClient

from app import agent, llm
from app.main import app


def _message(content, stop_reason="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": llm.MODEL,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


@pytest.fixture
def api(monkeypatch):
    """A real SDK client wired to a fake transport, so the request the SDK
    actually builds can be inspected."""
    seen = SimpleNamespace(requests=[], status=200, body=_message([{"type": "text", "text": " hi "}]))

    def handler(request):
        seen.requests.append(request)
        return httpx.Response(seen.status, json=seen.body)

    client = anthropic.Anthropic(
        api_key="sk-test",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(llm, "_client", lambda: client)
    return seen


def test_returns_text(api):
    assert llm.call_llm("s", "u") == "hi"


def test_skips_a_leading_thinking_block(api):
    api.body = _message([
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "the hint"},
    ])
    assert llm.call_llm("s", "u") == "the hint"


def test_requests_refusal_fallback_for_supported_models(api):
    llm.call_llm("s", "u")
    request = api.requests[0]
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    body = json.loads(request.content)
    assert body["fallbacks"] == "default"
    assert body["model"] == llm.MODEL
    assert body["max_tokens"] == llm.MAX_TOKENS


def test_no_fallback_for_other_models(api, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "claude-haiku-5-5")
    llm.call_llm("s", "u")
    assert "fallbacks" not in json.loads(api.requests[0].content)
    assert "anthropic-beta" not in api.requests[0].headers


@pytest.mark.parametrize("body", [
    _message([{"type": "thinking", "thinking": "", "signature": "sig"}], stop_reason="max_tokens"),
    _message([], stop_reason="refusal"),
])
def test_no_usable_text_raises(api, body):
    api.body = body
    with pytest.raises(llm.LLMUnavailable):
        llm.call_llm("s", "u")


def test_api_error_raises(api):
    api.status = 500
    api.body = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    with pytest.raises(llm.LLMUnavailable):
        llm.call_llm("s", "u")


def test_hint_endpoint_returns_503_when_model_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise llm.LLMUnavailable("down")

    monkeypatch.setattr(agent, "call_llm", unavailable)
    monkeypatch.setattr("app.tools.call_llm", unavailable)
    response = TestClient(app).post(
        "/v1/hint",
        json={"brief": "Write f(x)", "code": "def f(x):\n    return x\n", "rubric": "r"},
    )
    assert response.status_code == 503
