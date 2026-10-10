"""Runs learner code in real E2B sandboxes. Skipped unless E2B_API_KEY is
set; costs a few seconds of sandbox time per test:

    E2B_API_KEY=e2b_... pytest tests/test_sandbox_live.py
"""
import os

import pytest

from app import tools

# Read before the autouse fixture in conftest clears it for the other tests.
LIVE_KEY = os.environ.get("E2B_API_KEY")

pytestmark = pytest.mark.skipif(not LIVE_KEY, reason="E2B_API_KEY not set")

DOUBLE = "def f(x):\n    return x * 2\n"


@pytest.fixture(autouse=True)
def live(monkeypatch, fake_llm):
    monkeypatch.setenv("E2B_API_KEY", LIVE_KEY)
    monkeypatch.setenv("MWALIMU_SANDBOX", "e2b")
    monkeypatch.setattr(tools, "_execute_locally", lambda program: pytest.fail("ran learner code locally"))
    fake_llm.assertions = 'assert f(2) == 4, "doubles"'
    return fake_llm


def test_passing_code():
    assert tools.run_tests("Write f(x)", DOUBLE, "r").status == tools.PASSED


def test_failing_code():
    result = tools.run_tests("Write f(x)", "def f(x):\n    return x\n", "r")
    assert result.status == tools.FAILED
    assert "doubles" in result.message


def test_infinite_loop_times_out():
    assert tools.run_tests("Write f(x)", DOUBLE + "while True:\n    pass\n", "r").status == tools.TIMEOUT


def test_no_internet_access():
    code = DOUBLE + "import urllib.request\nurllib.request.urlopen('https://example.com', timeout=3)\n"
    assert tools.run_tests("Write f(x)", code, "r").status == tools.ERRORED


def test_no_server_secrets():
    code = DOUBLE + "import os\nassert 'ANTHROPIC_API_KEY' not in os.environ and 'E2B_API_KEY' not in os.environ\n"
    assert tools.run_tests("Write f(x)", code, "r").status == tools.PASSED
