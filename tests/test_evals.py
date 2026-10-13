"""The eval harness's own logic, with the agent and judge faked."""
import pytest

from evals import judge as judge_module
from evals import run_evals

SCENARIO = {
    "name": "s", "brief": "Write f(x)", "code": "def f(x):\n    pass", "rubric": "r",
    "prior_hints": [], "must_not_contain": ["x * 2"],
}


@pytest.fixture
def agent_says(monkeypatch):
    def set_result(hint, level=1, blocked=False, status="failed"):
        monkeypatch.setattr(run_evals, "run_agent", lambda **kw: {
            "hint": hint, "hint_level": level, "solved_leak_blocked": blocked, "test_status": status,
        })
    return set_result


@pytest.fixture
def judge_says(monkeypatch):
    def set_verdicts(*passed):
        monkeypatch.setattr(run_evals, "judge", lambda scenario, hint: [
            (f"rule {i}", p, f"reason {i}") for i, p in enumerate(passed)
        ])
    return set_verdicts


def test_clean_hint_passes(agent_says, judge_says):
    agent_says("What should f return for 2?")
    judge_says(True)
    assert run_evals.check(SCENARIO).ok


@pytest.mark.parametrize("hint", ["Try return x * 2", "```python\nreturn y\n```"])
def test_textual_leaks_count_as_leaks(agent_says, judge_says, hint):
    agent_says(hint)
    judge_says(True)
    outcome = run_evals.check(SCENARIO)
    assert not outcome.ok and outcome.leaked


def test_judge_leak_verdict_counts_as_leak(agent_says, judge_says):
    agent_says("First multiply, then hand back the product.")
    judge_says(False)
    outcome = run_evals.check(SCENARIO)
    assert not outcome.ok and outcome.leaked
    assert "judge" in outcome.message


def test_failed_scenario_rule_is_not_a_leak(agent_says, judge_says):
    agent_says("Looks right! Consider adding type hints.")
    judge_says(True, False)
    outcome = run_evals.check(SCENARIO)
    assert not outcome.ok and not outcome.leaked


def test_fallback_hint_is_a_failure_but_not_a_leak(agent_says, judge_says):
    agent_says("Try shrinking the problem.", blocked=True)
    judge_says(True)
    outcome = run_evals.check(SCENARIO)
    assert not outcome.ok and not outcome.leaked


def test_expected_test_status(agent_says, judge_says):
    agent_says("Nice work.", status="failed")
    judge_says(True)
    assert not run_evals.check({**SCENARIO, "expect_test_status": "passed"}).ok


def test_judge_errors_fail_every_rule(monkeypatch):
    class Broken:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("no network")

    monkeypatch.setattr(judge_module, "_client", lambda: Broken)
    verdicts = judge_module.judge({**SCENARIO, "judge_rules": ["extra"]}, "hint")
    assert len(verdicts) == 2
    assert not any(passed for _, passed, _ in verdicts)


def test_exit_status_reflects_leaks(agent_says, judge_says, monkeypatch, capsys):
    monkeypatch.setattr(run_evals, "SCENARIOS", [SCENARIO])
    agent_says("What should f return?")
    judge_says(True)
    assert run_evals.main(["--runs", "2"])
    agent_says("return x * 2")
    assert not run_evals.main(["--runs", "2"])
    assert "leak rate: 2/2" in capsys.readouterr().out
