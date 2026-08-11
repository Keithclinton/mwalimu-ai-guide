"""Context engineering lives here.

The whole idea: each step of the agent sees ONLY what it needs. This is where
you decide what's in the model's context, when, and why. In the interview, this
file is your evidence that you have "a real point of view on context
engineering" (their words).

Each function returns (system_prompt, user_prompt).
"""


def for_assess(state) -> tuple[str, str]:
    """Assessing where the learner is. Sees: rubric + code + test output.
    Does NOT need prior hints."""
    system = "You assess a learner's code against a rubric. Be brief and specific about what's missing."
    user = (
        f"RUBRIC:\n{state['rubric']}\n\n"
        f"LEARNER CODE:\n{state['code']}\n\n"
        f"TEST OUTPUT:\n{state['test_output']}"
    )
    return system, user


def for_hint(state) -> tuple[str, str]:
    """Writing the hint. Sees: brief, where they're stuck, hints already given
    (so it doesn't repeat), and the target hint level.
    TODO(you): deliberately decide what to include vs leave out here."""
    level_guide = {
        1: "Give the gentlest possible nudge. Ask a question, don't state the fix.",
        2: "Point clearly at the concept or line that's wrong, but don't write the code.",
        3: "Give a concrete next step, still stopping short of the full solution.",
    }
    system = (
        "You are Mwalimu, a coding tutor. You NEVER give the full solution. "
        "You give the smallest hint that helps the learner make the next move themselves. "
        f"{level_guide.get(state['hint_level'], level_guide[1])}"
    )
    prior = "\n".join(f"- {h}" for h in state["prior_hints"]) or "(none yet)"
    user = (
        f"TASK: {state['brief']}\n\n"
        f"WHERE THEY'RE STUCK:\n{state['test_output']}\n\n"
        f"HINTS ALREADY GIVEN (don't repeat these):\n{prior}"
    )
    return system, user
