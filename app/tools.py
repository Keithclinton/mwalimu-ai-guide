"""Tools the agent can use."""
import re
import subprocess
import sys
import textwrap

from app.llm import call_llm

ASSERTION_SYSTEM_PROMPT = (
    "You write Python `assert` statements that check a learner's function "
    "against a rubric. Output ONLY the assert statements, one per line — no "
    "explanation, no code fences, no function definition. Each line must be "
    "a complete, directly-executable `assert <call>, \"<message>\"` calling "
    "the function by name; assume the function is already defined above it."
)


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
    """
    match = re.search(r"def\s+(\w+)\s*\(", code) or re.search(r"\b(\w+)\s*\([^)]*\)", brief)
    func_name = match.group(1) if match else "the function"
    user = (
        f"TASK: {brief}\n\n"
        f"RUBRIC:\n{rubric}\n\n"
        f"FUNCTION TO TEST: {func_name}\n\n"
        f"LEARNER CODE (for the signature only — do not repeat it):\n{code}\n\n"
        "Write 2-3 assert statements covering the rubric's requirements."
    )
    try:
        raw = call_llm(ASSERTION_SYSTEM_PROMPT, user)
    except Exception:
        return ""
    return "\n".join(
        line.strip() for line in raw.splitlines() if line.strip().startswith("assert ")
    )


def run_tests(brief: str, code: str, rubric: str) -> str:
    """Run the learner's code against assertions derived from the rubric and
    report what actually fails, so later hints are grounded in real behavior
    instead of a guess.

    SAFETY: this executes untrusted, LLM-adjacent code (the learner's code,
    plus assert statements an LLM wrote) on the host. Running it in a fresh
    subprocess with a hard timeout stops an infinite loop or crash from
    hanging or taking down the server process, but that is a minimum, not a
    sandbox — see NOTES.md for what production needs beyond this.
    """
    if not code.strip():
        # Deterministic, not LLM-dependent: nothing has been written yet, so
        # there's nothing to execute. Phrased to match the "Code errored"
        # prefix so decide_level's stuck-on-fundamentals check (which looks
        # for that marker) catches this case too, instead of needing its own
        # special case for "no code at all".
        return "Code errored: no code has been written yet."
    assertions = _derive_assertions(brief, code, rubric)
    program = textwrap.dedent(code) + "\n\n" + assertions + "\n"
    try:
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            last_line = stderr.splitlines()[-1] if stderr else "Unknown error"
            if last_line.startswith("AssertionError"):
                return f"Rubric check failed: {last_line}"
            return f"Code errored:\n{stderr}"
        if not assertions:
            return "Code runs without error. Rubric-derived assertions unavailable this run."
        return "Code runs and passes all rubric-derived checks."
    except subprocess.TimeoutExpired:
        return "Code timed out (possible infinite loop)."


def get_rubric(project_id: str) -> str:
    """TODO(you): in a real system this fetches the rubric for a project. For
    now the rubric is passed in directly, so this is a placeholder for when you
    add a project store."""
    return "Rubric is provided directly in the request for now."
