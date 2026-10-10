import pytest

from app import agent, tools

STUB = "def is_prime(n):\n    pass\n"


@pytest.mark.parametrize("status, prior, expected", [
    (tools.PASSED, 0, 1),
    (tools.FAILED, 0, 1),
    (tools.UNCHECKED, 0, 1),
    (tools.ERRORED, 0, 2),
    (tools.TIMEOUT, 0, 2),
    (tools.EMPTY, 0, 2),
    (tools.FAILED, 1, 2),
    (tools.ERRORED, 1, 3),
    (tools.FAILED, 9, 3),  # capped
])
def test_decide_level(status, prior, expected):
    state = {"test_status": status, "test_output": "", "prior_hints": ["h"] * prior}
    assert agent.decide_level(state)["hint_level"] == expected


def test_decide_level_ignores_message_wording():
    # The old version searched the message for "errored"; a passing run whose
    # message happens to contain that word must not escalate.
    state = {"test_status": tools.PASSED, "test_output": "nothing errored", "prior_hints": []}
    assert agent.decide_level(state)["hint_level"] == 1


@pytest.mark.parametrize("hint", [
    "What does your function return for n = 1?",
    "```python\nx = 1\n```\nWhat should your function return here?",
    "Think about the loop bounds.",
])
def test_leak_check_allows_nudges(hint):
    assert not agent._hint_leaks_solution(hint, STUB)


@pytest.mark.parametrize("hint", [
    "```python\nfor i in range(2, n):\n    if n % i == 0:\n        return False\n```",
    "Try this:\n```python\nif n < 2:\n    return False",  # unclosed: cut off at max_tokens
    "def is_prime(n):\n    return n > 1",
])
def test_leak_check_flags_solutions(hint):
    assert agent._hint_leaks_solution(hint, STUB)


def test_persistent_leak_returns_fallback_hint(fake_llm):
    fake_llm.hint = "def is_prime(n):\n    return n > 1"
    result = agent.run_agent("Write is_prime(n)", STUB, "r", [])
    assert result["hint"] == agent.FALLBACK_HINT
    assert result["solved_leak_blocked"]
    assert fake_llm.hint_calls == 1 + agent.MAX_LEAK_RETRIES


def test_clean_hint_passes_through(fake_llm):
    result = agent.run_agent("Write is_prime(n)", STUB, "r", [])
    assert result["hint"] == fake_llm.hint
    assert not result["solved_leak_blocked"]
    assert fake_llm.hint_calls == 1
