"""The agent — a LangGraph state machine with four steps:

    assess  ->  decide_level  ->  draft_hint  ->  leak_check
                                        ^               |
                                        |___ retry (gentler level) if it leaks

Read app/context.py — it controls what each step is allowed to see. Keep the
model's context minimal and deliberate; that's the "context engineering" this
whole project is about.
"""
from typing import TypedDict
import re

from langgraph.graph import StateGraph, START, END

from app import context, tools
from app.llm import call_llm


# ---------------------------------------------------------------------------
# Agent state passed between nodes
# ---------------------------------------------------------------------------
class State(TypedDict):
    brief: str
    code: str
    rubric: str
    prior_hints: list[str]
    test_status: str   # one of the tools.* statuses; what decide_level branches on
    test_output: str   # the human-readable result that goes into the hint prompt
    hint_level: int
    hint: str
    solved_leak_blocked: bool
    leak_retries: int
    needs_retry: bool


MAX_LEAK_RETRIES = 2  # attempts beyond the first before we give up gracefully

# Returned instead of the drafted hint when every attempt leaked. Deliberately
# generic: anything derived from the leaked drafts could still carry the answer.
FALLBACK_HINT = (
    "Try shrinking the problem: what should your function do for the simplest "
    "possible input? Get that case working and run it, then add the next case."
)

# A complete fenced block, or an unclosed one running to the end (a reply cut
# off at max_tokens). Non-greedy, so a closing fence is never mistaken for the
# opening of the prose after it.
_FENCED_BLOCK = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def assess(state: State) -> State:
    """Run the learner's code against the rubric's tests and summarise where
    they are. Sees only code + rubric."""
    result = tools.run_tests(state["brief"], state["code"], state["rubric"])
    state["test_status"] = result.status
    state["test_output"] = result.message
    return state


def decide_level(state: State) -> State:
    """Pick hint level 1-3. Escalates with how many hints the learner has
    already had this session (repeated nudges that didn't land mean go more
    concrete), plus a bump if their code doesn't even run — that's a sign
    they're stuck on something more fundamental than a gentle nudge can fix,
    so skip past the pure Socratic-question level."""
    stuck_on_fundamentals = state["test_status"] in (tools.ERRORED, tools.TIMEOUT, tools.EMPTY)
    level = 1 + len(state["prior_hints"]) + (1 if stuck_on_fundamentals else 0)
    state["hint_level"] = max(1, min(level, 3))
    return state


def draft_hint(state: State) -> State:
    """Write the hint at the chosen level. context.for_hint reads
    state['hint_level'] to pick the escalation guidance in the system prompt,
    so demoting the level (leak_check, on retry) changes what gets drafted
    next without draft_hint needing to know why."""
    system, user = context.for_hint(state)
    state["hint"] = call_llm(system, user)
    return state


def _hint_leaks_solution(hint: str, code: str) -> bool:
    """Heuristic: does the hint contain what looks like a full implementation
    of the learner's function (a matching `def`, followed by a `return`)
    rather than a nudge? A second LLM call to judge this would double the
    cost and latency of every hint, so a cheap textual check is the
    deliberate tradeoff — it catches the "just wrote the whole function"
    failure mode without another round trip."""
    match = re.search(r"def\s+(\w+)\s*\(", code)
    func_name = match.group(1) if match else None
    if func_name:
        def_match = re.search(rf"def\s+{re.escape(func_name)}\s*\([^)]*\)\s*:", hint)
        if def_match and "return" in hint[def_match.end():]:
            return True
    # Only `return` inside a code block counts — a question like "what does
    # your function return?" is a nudge, not a leak.
    return any("return" in block for block in _FENCED_BLOCK.findall(hint))


def leak_check(state: State) -> State:
    """Reject hints that hand over the solution. If the retry budget isn't
    exhausted, demote the level (gentler next attempt) and signal a retry —
    draft_hint runs again with the demoted level instead of looping forever."""
    leaked = _hint_leaks_solution(state["hint"], state["code"])
    state["solved_leak_blocked"] = leaked
    retry = leaked and state["leak_retries"] < MAX_LEAK_RETRIES
    if retry:
        state["leak_retries"] += 1
        state["hint_level"] = max(1, state["hint_level"] - 1)
    state["needs_retry"] = retry
    return state


def _route_after_leak_check(state: State) -> str:
    return "draft_hint" if state["needs_retry"] else END


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------
def _build_graph():
    graph = StateGraph(State)
    graph.add_node("assess", assess)
    graph.add_node("decide_level", decide_level)
    graph.add_node("draft_hint", draft_hint)
    graph.add_node("leak_check", leak_check)

    graph.add_edge(START, "assess")
    graph.add_edge("assess", "decide_level")
    graph.add_edge("decide_level", "draft_hint")
    graph.add_edge("draft_hint", "leak_check")
    graph.add_conditional_edges("leak_check", _route_after_leak_check)

    return graph.compile()


_graph = _build_graph()


def run_agent(brief: str, code: str, rubric: str, prior_hints: list[str]) -> dict:
    state: State = {
        "brief": brief, "code": code, "rubric": rubric,
        "prior_hints": prior_hints, "test_status": "", "test_output": "",
        "hint_level": 1, "hint": "", "solved_leak_blocked": False,
        "leak_retries": 0, "needs_retry": False,
    }
    final_state = _graph.invoke(state)
    # Still leaking after the retry budget: don't ship the leaked draft.
    # solved_leak_blocked stays true so the caller can see it happened.
    hint = FALLBACK_HINT if final_state["solved_leak_blocked"] else final_state["hint"]
    return {
        "hint": hint,
        "hint_level": final_state["hint_level"],
        "solved_leak_blocked": final_state["solved_leak_blocked"],
    }
