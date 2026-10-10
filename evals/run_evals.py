"""Eval harness. Run: python -m evals.run_evals

This is your safety net: it checks that Mwalimu HINTS rather than SOLVES, and
that the hint level actually escalates instead of staying flat. When a
scenario fails, that's a real bug — see NOTES.md for the ones this caught.
"""
import json
import pathlib
import re
import sys

from app.agent import run_agent

SCENARIOS = json.loads((pathlib.Path(__file__).parent / "scenarios.json").read_text())


def _looks_like_full_solution(hint: str, code: str) -> bool:
    """Structural "is this actually a solution" check, written independently
    of app.agent._hint_leaks_solution on purpose — an eval shouldn't trust
    the system's own leak detector to judge whether it leaked; a bug in that
    detector should still show up here instead of passing silently."""
    match = re.search(r"def\s+(\w+)\s*\(", code)
    func_name = match.group(1) if match else None
    if func_name:
        def_match = re.search(rf"def\s+{re.escape(func_name)}\s*\([^)]*\)\s*:", hint)
        if def_match and "return" in hint[def_match.end():]:
            return True
    # Pair fences up first; scanning from any ``` would let a closing fence
    # start a "block" that runs into the prose after it.
    blocks = re.findall(r"```(.*?)(?:```|$)", hint, re.DOTALL)
    return any("return" in b for b in blocks)


def check(scenario) -> tuple[bool, str]:
    result = run_agent(
        brief=scenario["brief"],
        code=scenario["code"],
        rubric=scenario["rubric"],
        prior_hints=scenario["prior_hints"],
    )
    hint = result["hint"]
    hint_lower = hint.lower()

    # --- did it hint or did it solve? ---
    for banned in scenario.get("must_not_contain", []):
        if banned.lower() in hint_lower:
            return False, f"LEAKED solution fragment: {banned!r}"

    if not hint.strip():
        return False, "empty hint"

    if result["solved_leak_blocked"]:
        return False, "agent's own leak_check still flagged this hint after exhausting retries"

    if _looks_like_full_solution(hint, scenario["code"]):
        return False, f"hint reads like a full solution: {hint!r}"

    # --- sanity: level is always in range, regardless of scenario ---
    if not (1 <= result["hint_level"] <= 3):
        return False, f"hint_level out of range: {result['hint_level']}"

    # --- does the level escalate after a prior hint? ---
    if scenario["prior_hints"] and result["hint_level"] <= 1:
        return False, (
            f"level did not escalate despite {len(scenario['prior_hints'])} prior hint(s) "
            f"(got level {result['hint_level']})"
        )

    if "min_level" in scenario and result["hint_level"] < scenario["min_level"]:
        return False, f"expected hint_level >= {scenario['min_level']}, got {result['hint_level']}"

    return True, f"ok (level {result['hint_level']})"


def main():
    passed = 0
    for s in SCENARIOS:
        ok, msg = check(s)
        print(f"[{'PASS' if ok else 'FAIL'}] {s['name']}: {msg}")
        passed += ok
    print(f"\n{passed}/{len(SCENARIOS)} passed")
    return passed == len(SCENARIOS)


if __name__ == "__main__":
    # Non-zero exit on any failure, so CI marks the run red.
    sys.exit(0 if main() else 1)
