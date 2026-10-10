import pytest

from app import tools
from app.tools import is_safe_assert

DOUBLE = "def f(x):\n    return x * 2\n"


@pytest.mark.parametrize("line", [
    'assert is_prime(2) == True, "2 is prime"',
    "assert not is_prime(1)",
    "assert is_prime(-5) is False",
    "assert is_prime(n=7)",
    "assert is_prime([1, 2]) == (1, {'a': None})",
    "assert len(is_prime('ab')) == 2",
    "assert sorted(is_prime([3, 1]))[0] == 1",
    "assert abs(is_prime(0.5) - 0.25) < 1e-9",
])
def test_safe_assert_accepts_plain_checks(line):
    assert is_safe_assert(line, "is_prime")


@pytest.mark.parametrize("line", [
    'assert __import__("os").system("id")',
    "assert is_prime(2).__class__",
    "assert open('/etc/passwd')",
    "assert is_prime(len(open('x').read()))",
    "assert eval('1')",
    "assert is_prime(n)",                     # non-literal argument
    "assert is_prime(2) == os",               # bare name lookup
    "assert True",                            # doesn't test the function
    "assert is_prime(2); import os",          # two statements
    "import os",
    'assert is_prime(2), f"{secret}"',        # f-string message
    "assert (lambda: 1)()",
    "assert [i for i in range(3)]",
    "assert is_prime(*[2])",
    "assert is_prime(**{'n': 2})",
    "assert (x := is_prime(2))",
    "assert is_prime(2",                      # syntax error
    "assert other_func(2)",
])
def test_safe_assert_rejects_anything_else(line):
    assert not is_safe_assert(line, "is_prime")


def test_unsafe_generated_asserts_are_dropped(fake_llm):
    fake_llm.assertions = (
        'assert f(2) == 4, "doubles"\n'
        'assert __import__("os").system("echo pwned")\n'
        "Here are your asserts!\n"
    )
    assert tools._derive_assertions("Write f(x)", DOUBLE, "r") == 'assert f(2) == 4, "doubles"'


def test_no_function_name_means_no_model_call(fake_llm, monkeypatch):
    monkeypatch.setattr(tools, "call_llm", lambda s, u: pytest.fail("should not call the model"))
    assert tools._derive_assertions("Do something", "x = 1", "r") == ""


def test_learner_code_cannot_read_the_api_key(fake_llm):
    fake_llm.assertions = 'assert f(2) == 4, "doubles"'
    code = DOUBLE + "import os\nraise Exception(os.environ.get('ANTHROPIC_API_KEY'))\n"
    result = tools.run_tests("Write f(x)", code, "r")
    assert result.status == tools.ERRORED
    assert "SECRET" not in result.message


def test_stderr_is_capped(fake_llm):
    result = tools.run_tests("Write f(x)", DOUBLE + "raise Exception('x' * 5000)\n", "r")
    assert result.status == tools.ERRORED
    assert len(result.message) < tools.MAX_STDERR_CHARS + 50


@pytest.mark.parametrize("exit_call", ["import sys\nsys.exit(0)", "import os\nos._exit(0)"])
def test_exiting_early_is_not_a_pass(fake_llm, exit_call):
    fake_llm.assertions = 'assert f(2) == 5, "wrong on purpose"'
    result = tools.run_tests("Write f(x)", DOUBLE + exit_call + "\n", "r")
    assert result.status == tools.ERRORED
    assert "exited before" in result.message


@pytest.mark.parametrize("code, assertions, status", [
    (DOUBLE, 'assert f(2) == 4, "doubles"', tools.PASSED),
    ("def f(x):\n    return x\n", 'assert f(2) == 4, "doubles"', tools.FAILED),
    ("def f(x)\n    return x\n", 'assert f(2) == 4, "doubles"', tools.ERRORED),
    (DOUBLE + "while True:\n    pass\n", "", tools.TIMEOUT),
    (DOUBLE, "", tools.UNCHECKED),
    ("   \n", "", tools.EMPTY),
])
def test_run_tests_statuses(fake_llm, code, assertions, status):
    fake_llm.assertions = assertions
    assert tools.run_tests("Write f(x)", code, "r").status == status
