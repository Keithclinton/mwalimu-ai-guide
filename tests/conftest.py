"""Shared fixtures. Nothing in this suite calls the real model."""
import os
import tempfile

# Must be set before app.db is imported: the engine is built at import time.
TEST_DB = os.path.join(tempfile.gettempdir(), f"mwalimu-test-{os.getpid()}.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from types import SimpleNamespace  # noqa: E402

import anthropic  # noqa: E402
import httpx2 as httpx  # noqa: E402  (anthropic 1.x is built on httpx2; plain httpx objects are rejected)
import pytest  # noqa: E402

from app import accounts, agent, llm, tools  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-SECRET-123")


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def make_customer():
    """make_customer(rate_limit=60) -> (customer_id, api_key)"""
    def make(name="Acme Academy", rate_limit=60):
        with SessionLocal() as db:
            customer = accounts.create_customer(db, name, rate_limit)
            key = accounts.issue_key(db, customer.id)
            db.commit()
            return customer.id, key
    return make


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace the model with canned replies. Set `.assertions` for the
    rubric-check call and `.hint` for the hint call."""

    class FakeLLM:
        assertions = ""
        hint = "What happens when n is 1?"
        hint_calls = 0

        def for_tools(self, system, user):
            return self.assertions

        def for_agent(self, system, user):
            self.hint_calls += 1
            self.prompts.append(user)
            return self.hint

    fake = FakeLLM()
    fake.prompts = []
    monkeypatch.setattr(tools, "call_llm", fake.for_tools)
    monkeypatch.setattr(agent, "call_llm", fake.for_agent)
    return fake


def message(content, stop_reason="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": llm.MODEL,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


@pytest.fixture
def api(monkeypatch):
    """A real SDK client wired to a fake transport, so the request the SDK
    actually builds can be inspected."""
    seen = SimpleNamespace(requests=[], status=200, body=message([{"type": "text", "text": " hi "}]))

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
