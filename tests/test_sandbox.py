"""The E2B backend, against a fake Sandbox. tests/test_sandbox_live.py runs
the same paths against real E2B when E2B_API_KEY is set."""
import logging
import re
from types import SimpleNamespace

import pytest
from e2b import (
    AuthenticationException,
    CommandExitException,
    CommandResult,
    SandboxException,
    SandboxNotRunningException,
    SandboxUnreachableException,
    ServiceBusyException,
    TimeoutException,
)
from fastapi.testclient import TestClient

from app import tools
from app.main import app

DOUBLE = "def f(x):\n    return x * 2\n"


def _marker(program):
    return re.search(r"print\('([0-9a-f]{32})'\)", program).group(1)


def ok(stdout=""):
    return lambda program: CommandResult(stdout=stdout or _marker(program) + "\n", stderr="", exit_code=0, error=None)


def exits(code, stderr=""):
    def respond(program):
        raise CommandExitException(stdout="", stderr=stderr, exit_code=code, error=None)
    return respond


def raises(exc):
    def respond(program):
        raise exc
    return respond


@pytest.fixture
def e2b(monkeypatch, fake_llm):
    monkeypatch.setenv("MWALIMU_SANDBOX", "e2b")
    monkeypatch.setattr(tools, "_execute_locally", lambda program: pytest.fail("ran learner code locally"))
    fake_llm.assertions = 'assert f(2) == 4, "doubles"'
    state = SimpleNamespace(create_kwargs=None, create_error=None, program=None, command=None,
                            killed=False, respond=ok())

    class FakeSandbox:
        @classmethod
        def create(cls, **kwargs):
            state.create_kwargs = kwargs
            if state.create_error:
                raise state.create_error
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            state.killed = True

        files = SimpleNamespace(write=lambda path, data: setattr(state, "program", data))

        def run(command, timeout=None):
            state.command = command
            return state.respond(state.program)

        commands = SimpleNamespace(run=run)

    monkeypatch.setattr(tools, "Sandbox", FakeSandbox)
    return state


@pytest.mark.parametrize("env, expected", [
    ({}, "local"),
    ({"E2B_API_KEY": "e2b_x"}, "e2b"),
    ({"E2B_API_KEY": "e2b_x", "MWALIMU_SANDBOX": "local"}, "local"),
    ({"MWALIMU_SANDBOX": " E2B "}, "e2b"),
])
def test_backend_selection(monkeypatch, env, expected):
    monkeypatch.delenv("MWALIMU_SANDBOX", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert tools.sandbox_backend() == expected


def test_unknown_backend_is_an_error(monkeypatch, fake_llm):
    monkeypatch.setenv("MWALIMU_SANDBOX", "docker")
    with pytest.raises(ValueError):
        tools.run_tests("Write f(x)", DOUBLE, "r")


def test_sandbox_is_isolated_and_killed(e2b):
    assert tools.run_tests("Write f(x)", DOUBLE, "r").status == tools.PASSED
    assert e2b.create_kwargs["allow_internet_access"] is False
    assert "envs" not in e2b.create_kwargs
    assert e2b.killed
    assert e2b.command.startswith(f"timeout -k 2 {tools.RUN_TIMEOUT_SECONDS} python3 -I ")
    assert DOUBLE in e2b.program and 'assert f(2) == 4, "doubles"' in e2b.program


@pytest.mark.parametrize("respond, status", [
    (exits(1, "Traceback ...\nAssertionError: doubles"), tools.FAILED),
    (exits(1, "  File ...\nSyntaxError: invalid syntax"), tools.ERRORED),
    (exits(124), tools.TIMEOUT),
    (exits(137), tools.TIMEOUT),
    (raises(TimeoutException("deadline exceeded")), tools.TIMEOUT),
    (ok(stdout="no marker here\n"), tools.ERRORED),  # exited before the asserts
])
def test_results_are_classified(e2b, respond, status):
    e2b.respond = respond
    assert tools.run_tests("Write f(x)", DOUBLE, "r").status == status
    assert e2b.killed


def test_assertion_message_is_reported(e2b):
    e2b.respond = exits(1, "Traceback ...\nAssertionError: doubles")
    assert tools.run_tests("Write f(x)", DOUBLE, "r").message == "Rubric check failed: AssertionError: doubles"


@pytest.mark.parametrize("where, error", [
    ("create", SandboxException("rate limited")),
    ("create", AuthenticationException("401: invalid API key")),  # not a SandboxException
    ("create", ServiceBusyException("busy")),
    ("create", ConnectionError("network down")),
    ("run", SandboxException("rate limited")),
    # TimeoutException subclasses: the sandbox failed, not the learner's code.
    ("run", SandboxUnreachableException("unreachable")),
    ("run", SandboxNotRunningException("gone")),
])
def test_sandbox_outage_never_falls_back_to_local(e2b, where, error, caplog):
    if where == "create":
        e2b.create_error = error
    else:
        e2b.respond = raises(error)
    with caplog.at_level(logging.WARNING):
        result = tools.run_tests("Write f(x)", DOUBLE, "r")
    assert result.status == tools.UNCHECKED
    assert "sandbox unavailable" in caplog.text


def test_startup_warns_when_running_code_locally(caplog):
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    assert "ON THIS SERVER" in caplog.text


def test_startup_is_quiet_with_e2b(monkeypatch, caplog):
    monkeypatch.setenv("E2B_API_KEY", "e2b_x")
    monkeypatch.setenv("MWALIMU_SANDBOX", "e2b")
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    assert "ON THIS SERVER" not in caplog.text
    assert "not set" not in caplog.text


def test_startup_flags_e2b_without_a_key(monkeypatch, caplog):
    monkeypatch.setenv("MWALIMU_SANDBOX", "e2b")
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    assert "E2B_API_KEY is not set" in caplog.text


def test_startup_flags_a_missing_anthropic_key(monkeypatch, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    assert "ANTHROPIC_API_KEY is not set" in caplog.text


def test_startup_refuses_an_unknown_backend(monkeypatch):
    monkeypatch.setenv("MWALIMU_SANDBOX", "docker")
    with pytest.raises(RuntimeError), TestClient(app):
        pass
