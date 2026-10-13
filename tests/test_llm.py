import json

import pytest

from app import llm
from tests.conftest import message as _message


def test_returns_text(api):
    assert llm.call_llm("s", "u") == "hi"


def test_skips_a_leading_thinking_block(api):
    api.body = _message([
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "the hint"},
    ])
    assert llm.call_llm("s", "u") == "the hint"


def test_requests_refusal_fallback_for_supported_models(api, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "claude-opus-5-5")  # independent of any .env choice
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


def test_usage_is_collected_inside_track_usage(api):
    api.body = {**_message([{"type": "text", "text": "x"}]), "usage": {"input_tokens": 7, "output_tokens": 3}}
    with llm.track_usage() as totals:
        llm.call_llm("s", "u")
        llm.call_llm("s", "u")
    assert totals == {"input_tokens": 14, "output_tokens": 6}
    llm.call_llm("s", "u")  # outside the block: not counted, no error
    assert totals == {"input_tokens": 14, "output_tokens": 6}
