"""Eval harness. These call the real model and cost money.

    python -m evals.run_evals                 # each scenario once
    python -m evals.run_evals --runs 5        # pass rate per scenario
    python -m evals.run_evals --show-hints    # print every hint
    python -m evals.run_evals --no-judge      # skip the LLM judge (cheaper)

This is your safety net: it checks that Mwalimu HINTS rather than SOLVES, and
that the hint level actually escalates instead of staying flat. The model's
output varies between runs, so one pass proves little: run each scenario
several times and read the pass rate. The leak rate (how many hints learners
would have seen that gave the answer away) is the headline number.

Exit status is non-zero if any hint leaked or any scenario's pass rate is
below --min-pass-rate.
"""
import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass

from app.agent import run_agent
from evals.judge import JUDGE_MODEL, judge

SCENARIOS = json.loads((pathlib.Path(__file__).parent / "scenarios.json").read_text(encoding="utf-8"))


@dataclass
class Outcome:
    ok: bool
    leaked: bool   # the learner would have seen a hint that gives the answer away
    message: str
    hint: str = ""


def _looks_like_full_solution(hint: str, code: str) -> bool:
    """Cheap structural check: a matching `def` followed by `return`, or a
    `return` inside a code block. It's close to the agent's own heuristic,
    so it shares its blind spots; the independent check is the LLM judge
    (evals/judge.py)."""
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


def check(scenario, use_judge: bool = True) -> Outcome:
    result = run_agent(
        brief=scenario["brief"],
        code=scenario["code"],
        rubric=scenario["rubric"],
        prior_hints=scenario["prior_hints"],
    )
    hint = result["hint"]
    hint_lower = hint.lower()

    def fail(message, leaked=False):
        return Outcome(False, leaked, message, hint)

    # --- did it hint or did it solve? ---
    for banned in scenario.get("must_not_contain", []):
        if banned.lower() in hint_lower:
            return fail(f"LEAKED fragment {banned!r}", leaked=True)

    if not hint.strip():
        return fail("empty hint")

    if _looks_like_full_solution(hint, scenario["code"]):
        return fail("hint reads like a full solution", leaked=True)

    if result["solved_leak_blocked"]:
        # The learner saw the generic fallback, not the leak, so this is a
        # quality failure rather than a leak.
        return fail("every draft leaked; the fallback hint was returned")

    # --- did the test run see what it should have? ---
    expected_status = scenario.get("expect_test_status")
    if expected_status and result["test_status"] != expected_status:
        return fail(f"expected test_status {expected_status!r}, got {result['test_status']!r}")

    # --- sanity: level is always in range, regardless of scenario ---
    if not (1 <= result["hint_level"] <= 3):
        return fail(f"hint_level out of range: {result['hint_level']}")

    # --- does the level escalate after a prior hint? ---
    if scenario["prior_hints"] and result["hint_level"] <= 1:
        return fail(
            f"level did not escalate despite {len(scenario['prior_hints'])} prior hint(s) "
            f"(got level {result['hint_level']})"
        )

    if "min_level" in scenario and result["hint_level"] < scenario["min_level"]:
        return fail(f"expected hint_level >= {scenario['min_level']}, got {result['hint_level']}")

    # --- independent judge: catches pseudocode, prose walkthroughs, and the
    # scenario's own rules, which no regex can ---
    if use_judge:
        for i, (rule, passed, reason) in enumerate(judge(scenario, hint, result["hint_level"])):
            if not passed:
                return fail(f"judge: {reason}", leaked=(i == 0))

    return Outcome(True, False, f"ok (level {result['hint_level']})", hint)


def main(argv=None) -> bool:
    parser = argparse.ArgumentParser(prog="python -m evals.run_evals")
    parser.add_argument("--runs", type=int, default=1, help="runs per scenario")
    parser.add_argument("--min-pass-rate", type=float, default=0.8,
                        help="fail if any scenario passes less often than this (0-1)")
    parser.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    parser.add_argument("--show-hints", action="store_true", help="print every hint")
    parser.add_argument("--only", help="run only scenarios whose name contains this")
    args = parser.parse_args(argv)

    scenarios = [s for s in SCENARIOS if not args.only or args.only in s["name"]]
    if not args.no_judge:
        print(f"judge: {JUDGE_MODEL}")

    total_runs = total_leaks = 0
    below_threshold = []
    for s in scenarios:
        outcomes = [check(s, use_judge=not args.no_judge) for _ in range(args.runs)]
        passed = sum(o.ok for o in outcomes)
        leaks = sum(o.leaked for o in outcomes)
        total_runs += len(outcomes)
        total_leaks += leaks
        rate = passed / len(outcomes)
        if rate < args.min_pass_rate:
            below_threshold.append(s["name"])
        label = "PASS" if rate >= args.min_pass_rate and not leaks else "FAIL"
        print(f"[{label}] {s['name']}: {passed}/{len(outcomes)} passed, {leaks} leaked")
        for o in outcomes:
            if not o.ok:
                print(f"         - {o.message}")
            if args.show_hints or not o.ok:
                print(f"           hint: {o.hint!r}")

    print(f"\nleak rate: {total_leaks}/{total_runs} hints ({total_leaks / total_runs:.0%})")
    if below_threshold:
        print(f"below {args.min_pass_rate:.0%} pass rate: {', '.join(below_threshold)}")
    return total_leaks == 0 and not below_threshold


if __name__ == "__main__":
    # Non-zero exit on any failure, so CI marks the run red.
    sys.exit(0 if main() else 1)
