"""Tools the agent can use.

Configuration:
    MWALIMU_SANDBOX   where learner code runs: "e2b" (an isolated microVM per
                      run, no internet) or "local" (a subprocess on this
                      server, for development only). Defaults to "e2b" when
                      E2B_API_KEY is set, otherwise "local".
    E2B_API_KEY       required for the e2b sandbox
"""
import ast
import logging
import os
import re
import secrets
import shlex
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from typing import Optional

from e2b import (
    CommandExitException,
    Sandbox,
    SandboxNotRunningException,
    SandboxUnreachableException,
    TimeoutException,
)

from app import context
from app.llm import call_llm

logger = logging.getLogger("uvicorn.error")

# Test output ends up in the hint prompt. The end of a traceback is the useful
# part; anything longer is either noise or someone stuffing the prompt.
MAX_STDERR_CHARS = 500

# Wall-clock limit for the learner's program, in seconds.
RUN_TIMEOUT_SECONDS = 5

# Builtins a generated assert may call besides the learner's function, e.g.
# `assert len(f("ab")) == 2`. None of them can reach the file system, the
# network, or an object's attributes.
SAFE_BUILTINS = frozenset({
    "abs", "all", "any", "bool", "float", "int", "isinstance", "len", "list",
    "max", "min", "round", "set", "sorted", "str", "sum", "tuple",
})

# Everything else — attribute access, lambdas, comprehensions, f-strings,
# walrus, starred args — is rejected. Attribute access is the important one:
# it's how `().__class__.__base__...` style escapes start.
_ALLOWED_NODES = (
    ast.Assert, ast.Call, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.BinOp,
    ast.Constant, ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Subscript,
    ast.Slice, ast.Name, ast.keyword, ast.Load,
    ast.cmpop, ast.boolop, ast.unaryop, ast.operator,
)


# Statuses run_tests can report. decide_level branches on these, never on the
# wording of the message.
PASSED = "passed"        # ran, and every rubric assert passed
UNCHECKED = "unchecked"  # ran without error, but no usable asserts this run
FAILED = "failed"        # a rubric assert failed
ERRORED = "errored"      # crashed, didn't parse, or exited before the asserts
TIMEOUT = "timeout"      # hit the wall-clock limit
EMPTY = "empty"          # nothing written yet


@dataclass(frozen=True)
class RunResult:
    status: str
    message: str  # human-readable; goes into the hint prompt


def _function_name(brief: str, code: str) -> Optional[str]:
    match = re.search(r"def\s+(\w+)\s*\(", code) or re.search(r"\b(\w+)\s*\([^)]*\)", brief)
    return match.group(1) if match else None


def _is_literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def is_safe_assert(line: str, func_name: str) -> bool:
    """True if `line` is a single assert that only calls the learner's
    function (with literal arguments) and a few harmless builtins.

    The asserts come from a model that has read the learner's code, so a
    prompt injection in that code can steer what gets written — and these
    lines are executed. Checking the prefix isn't enough:
    `assert __import__("os").system(...)` starts with "assert " too."""
    try:
        tree = ast.parse(line)
    except SyntaxError:
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assert):
        return False
    stmt = tree.body[0]
    if stmt.msg is not None and not (
        isinstance(stmt.msg, ast.Constant) and isinstance(stmt.msg.value, str)
    ):
        return False

    calls_target = False
    call_funcs = set()
    for node in ast.walk(stmt.test):
        if not isinstance(node, _ALLOWED_NODES):
            return False
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return False
            if node.func.id == func_name:
                args = [*node.args, *(k.value for k in node.keywords)]
                if any(k.arg is None for k in node.keywords) or not all(map(_is_literal, args)):
                    return False
                calls_target = True
            elif node.func.id not in SAFE_BUILTINS:
                return False
            call_funcs.add(id(node.func))
    # A bare name anywhere other than the function position of a call (e.g.
    # `assert os`) is a variable lookup we didn't vet.
    for node in ast.walk(stmt.test):
        if isinstance(node, ast.Name) and id(node) not in call_funcs:
            return False
    # `assert True` passes no matter what the learner wrote; it isn't a check.
    return calls_target


def _derive_assertions(brief: str, code: str, rubric: str) -> str:
    """Ask the LLM for 2-3 assert statements that check the rubric's
    requirements against the learner's function. A rubric is free text
    ("handles n<2, even numbers, and non-primes") — turning that into
    runnable checks needs the model; a keyword/regex parser can't do it
    generically. If the call fails or returns nothing usable, we fall back to
    running the code with no extra assertions rather than blocking the hint
    entirely — a missing assertion is a weaker test run, not a broken one.

    Takes `brief` (not just code+rubric) so it has an authoritative function
    name even when the learner's code doesn't define one yet — a rubric like
    "handles n<2, even numbers, and non-primes" doesn't always name the
    function, but the brief ("Write is_prime(n)...") usually does.

    Every line the model returns goes through is_safe_assert before it can
    run; anything else is dropped.
    """
    func_name = _function_name(brief, code)
    if func_name is None:
        return ""
    system, user = context.for_assertions(brief, code, rubric, func_name)
    try:
        raw = call_llm(system, user)
    except Exception:
        return ""
    return "\n".join(
        line.strip() for line in raw.splitlines() if is_safe_assert(line.strip(), func_name)
    )


@dataclass(frozen=True)
class Execution:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxUnavailable(Exception):
    """The sandbox couldn't run the code (E2B outage, rate limit, bad key).
    The code is then not run at all; it never falls back to this server."""


def sandbox_backend() -> str:
    default = "e2b" if os.environ.get("E2B_API_KEY") else "local"
    return os.environ.get("MWALIMU_SANDBOX", default).strip().lower()


def _execute(program: str) -> Execution:
    backend = sandbox_backend()
    if backend == "e2b":
        return _execute_in_e2b(program)
    if backend == "local":
        return _execute_locally(program)
    raise ValueError(f"MWALIMU_SANDBOX must be 'e2b' or 'local', not {backend!r}")


def _execute_in_e2b(program: str) -> Execution:
    """Run the program in a fresh E2B microVM: its own kernel, no internet
    access, none of our environment variables, killed as soon as the run
    ends. Nothing the learner's code does can reach this server, our
    secrets, or the network."""
    path = "/tmp/program.py"
    # `timeout` sends SIGTERM after RUN_TIMEOUT_SECONDS and SIGKILL 2s later,
    # for code that ignores SIGTERM. The SDK timeout is a backstop.
    command = f"timeout -k 2 {RUN_TIMEOUT_SECONDS} python3 -I {shlex.quote(path)}"
    try:
        with Sandbox.create(timeout=60, allow_internet_access=False) as sandbox:
            sandbox.files.write(path, program)
            try:
                result = sandbox.commands.run(command, timeout=RUN_TIMEOUT_SECONDS + 10)
            except CommandExitException as e:
                # 124: timed out on SIGTERM; 137: needed SIGKILL.
                return Execution(e.exit_code, e.stdout, e.stderr, timed_out=e.exit_code in (124, 137))
            except (SandboxNotRunningException, SandboxUnreachableException):
                # Subclasses of TimeoutException, but the sandbox failed, not
                # the learner's code. Handled by the outer except.
                raise
            except TimeoutException:
                return Execution(-1, "", "", timed_out=True)
            return Execution(result.exit_code, result.stdout, result.stderr)
    except Exception as e:
        # The SDK's errors don't share a base class (AuthenticationException,
        # ServiceBusyException and network errors sit outside
        # SandboxException), and whatever went wrong, the answer is the same:
        # don't run the code, and never fall back to running it here.
        raise SandboxUnavailable(f"{type(e).__name__}: {e}") from e


def _execute_locally(program: str) -> Execution:
    """Development only: runs learner code as a subprocess of this server."""
    try:
        result = subprocess.run(
            # -I: ignore PYTHON* env vars and user site-packages, and keep the
            # working directory off sys.path.
            [sys.executable, "-I", "-c", program],
            capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS, env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return Execution(-1, "", "", timed_out=True)
    return Execution(result.returncode, result.stdout, result.stderr)


def _child_env() -> dict:
    """The environment learner code runs with locally: just enough to start
    Python. Inheriting ours would hand it ANTHROPIC_API_KEY, and anything it
    can read it can print to stderr — which goes into the hint prompt."""
    if sys.platform == "win32":
        # Python on Windows can fail to start without SYSTEMROOT, and without
        # SYSTEMDRIVE Windows writes shell caches to a literal "%SystemDrive%"
        # folder in the working directory.
        return {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"),
        }
    return {"PATH": "/usr/bin:/bin"}


def _tail(text: str, limit: int = MAX_STDERR_CHARS) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


def run_tests(brief: str, code: str, rubric: str) -> RunResult:
    """Run the learner's code against assertions derived from the rubric and
    report what actually fails, so later hints are grounded in real behavior
    instead of a guess.

    SAFETY: this executes untrusted, LLM-adjacent code (the learner's code,
    plus assert statements an LLM wrote). In production it runs in an E2B
    sandbox (see _execute_in_e2b); the local backend is for development.
    """
    if not code.strip():
        # Deterministic, not LLM-dependent: nothing has been written yet, so
        # there's nothing to execute.
        return RunResult(EMPTY, "No code has been written yet.")
    assertions = _derive_assertions(brief, code, rubric)
    # Exit code 0 alone doesn't prove the asserts ran: sys.exit(0) in the
    # learner's code would skip them. Print a fresh random marker after the
    # asserts and only count the run as passing if it shows up. It's new
    # every run, so the learner's code can't print it ahead of time.
    marker = secrets.token_hex(16)
    program = (
        textwrap.dedent(code) + "\n\n" + assertions + "\n"
        + f"print({marker!r})\n"
    )
    try:
        result = _execute(program)
    except SandboxUnavailable as e:
        # Hint without running the code rather than fail the request.
        logger.warning("sandbox unavailable: %s", e)
        return RunResult(UNCHECKED, "The code couldn't be run this time, so there are no test results.")
    if result.timed_out:
        return RunResult(TIMEOUT, "Code timed out (possible infinite loop).")
    if result.exit_code != 0:
        stderr = result.stderr.strip()
        last_line = stderr.splitlines()[-1] if stderr else "Unknown error"
        if last_line.startswith("AssertionError"):
            return RunResult(FAILED, f"Rubric check failed: {_tail(last_line)}")
        return RunResult(ERRORED, f"Code errored:\n{_tail(stderr)}")
    if marker not in result.stdout:
        return RunResult(ERRORED, "Code errored: the program exited before the rubric checks could run.")
    if not assertions:
        return RunResult(UNCHECKED, "Code runs without error. Rubric-derived assertions unavailable this run.")
    return RunResult(PASSED, "Code runs and passes all rubric-derived checks.")


def get_rubric(project_id: str) -> str:
    """TODO(you): in a real system this fetches the rubric for a project. For
    now the rubric is passed in directly, so this is a placeholder for when you
    add a project store."""
    return "Rubric is provided directly in the request for now."
