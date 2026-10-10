"""Shared fixtures. Nothing in this suite calls the real model."""
import pytest

from app import agent, tools


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-SECRET-123")


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
            return self.hint

    fake = FakeLLM()
    monkeypatch.setattr(tools, "call_llm", fake.for_tools)
    monkeypatch.setattr(agent, "call_llm", fake.for_agent)
    return fake
