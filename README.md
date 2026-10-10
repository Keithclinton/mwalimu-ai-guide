# Mwalimu — an AI project-guide for coding learners

Mwalimu ("teacher" in Kiswahili) is a small AI agent that helps a learner work
through a coding project **without giving away the answer**. Given a project
brief, a rubric, and the learner's current code, it returns a *scaffolded
hint* — the smallest nudge that moves them forward — and escalates only when
the learner is genuinely stuck.

Built to demonstrate: production-style FastAPI, an LLM agent on a real
LangGraph `StateGraph` (not just a sequence of function calls), deliberate
context engineering, LLM-grounded tool use, and an eval harness that actually
catches regressions.

---

## Architecture

```
learner code + brief + rubric
        │
        ▼
   FastAPI  POST /hint                          (app/main.py)
        │
        ▼
   LangGraph StateGraph                          (app/agent.py)
   ┌─────────────────────────────────────────────────────────┐
   │                                                          │
   │   assess ──▶ decide_level ──▶ draft_hint ──▶ leak_check  │
   │                                    ▲               │     │
   │                                    └── retry ───────┘     │
   │                              (gentler level, if it leaked) │
   └─────────────────────────────────────────────────────────┘
        │                    │                     │
        ▼                    ▼                     ▼
   tools.run_tests    app/context.py         app/llm.py (Anthropic call)
   (app/tools.py)      builds each
                        step's prompt
```

- **`assess`** runs the learner's code against a couple of rubric-derived
  `assert` statements in a sandboxed subprocess, and records what actually
  failed.
- **`decide_level`** picks hint level 1–3: escalates with how many hints
  the learner has already burned through, plus a bump if the code doesn't
  even run (a different, more fundamental kind of stuck than a logic bug).
- **`draft_hint`** calls Claude with a level-specific system prompt.
- **`leak_check`** rejects hints that read like a full solution and, up to
  a retry budget, loops back to `draft_hint` with the level demoted —
  rather than either shipping a leaked answer or looping forever.

## Why it's built this way (the interesting decisions)

- **Context engineering.** Each node sees only what it needs —
  `context.for_assess` gets the rubric, code, and test output (not prior
  hints); `context.for_hint` gets the brief, where they're stuck, and hints
  already given (so it doesn't repeat itself), but never the raw test
  output verbatim beyond what's needed. Deciding what's in scope at each
  step, and why, is the actual point of the project. (`app/context.py`)
- **Hint, don't solve — with a bounded retry, not just a flag.** A
  heuristic check (`agent._hint_leaks_solution`) looks for a `def` matching
  the learner's function followed by a `return` — i.e. "did this read like
  a working implementation, not a nudge?" If so, the graph retries
  `draft_hint` at a demoted level up to `MAX_LEAK_RETRIES` times. If every
  attempt leaks, it returns a generic fallback hint instead of the leaked
  draft, with `solved_leak_blocked: true` so the caller knows. A heuristic
  instead of a second LLM judgment call is a deliberate cost/latency
  tradeoff, not an oversight.
- **Rubric-grounded assessment, not just "does it crash."** Rubrics are
  free text ("handles n<2, even numbers, and non-primes") — turning that
  into runnable checks needs the model, so `tools._derive_assertions` asks
  the LLM for 2–3 `assert` statements from the brief + rubric, then runs
  them alongside the learner's code in the same subprocess. Empty code is
  short-circuited deterministically rather than trusted to the LLM's
  assertions to catch it (see `NOTES.md` for the bug that taught me why).
- **Evals from day one, adversarial by design.** `evals/` doesn't just
  check the happy path — it includes empty code, already-correct code, a
  learner trying to prompt-inject their way to the full solution, an
  escalation-cap stress test, a syntax error, and a non-prime problem (to
  catch anything secretly hardcoded to the `is_prime` example). Every
  scenario checks both "did it hint or did it solve" (multiple independent
  signals, not just one string match) and "did the level actually escalate."

## Safety note: executing learner code

`tools.run_tests` executes untrusted code — the learner's submission, plus
`assert` statements an LLM wrote — in a subprocess with a 5-second timeout
and a stripped-down environment (so it can't read `ANTHROPIC_API_KEY`), and
caps the stderr that flows back into the hint prompt. That stops one bad run
from hanging the server or leaking the key; it is **not** a sandbox (no
filesystem/network/CPU isolation). `NOTES.md` has the full writeup of what's
missing and how I'd actually sandbox this for production.

## Run it

```bash
git clone <this-repo-url>
cd mwalimu-ai-guide

python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1

pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-...  # Windows PowerShell: $env:ANTHROPIC_API_KEY = "sk-..."

uvicorn app.main:app --reload
```

```bash
# in another terminal
curl -X POST localhost:8000/hint -H "content-type: application/json" \
  -d '{"brief":"Write is_prime(n)","code":"def is_prime(n):\n    return True","rubric":"handles n<2, even numbers, and non-primes"}'
```

Send the same request again with `"prior_hints": ["<the hint you just got>"]`
to see the level escalate.

### Run the evals

```bash
python -m evals.run_evals
```

## What's here

1. `app/agent.py` — the LangGraph `StateGraph`: four nodes, one conditional
   retry edge.
2. `app/context.py` — per-step prompt assembly; the context-engineering
   surface of the project.
3. `app/tools.py` — `run_tests`, executing learner code + LLM-derived
   assertions inside the safety guard described above.
4. `app/llm.py` — the single Anthropic call site, shared by `agent.py` and
   `tools.py`.
5. `evals/run_evals.py` + `evals/scenarios.json` — 8 scenarios, adversarial
   ones included, checking both leak-avoidance and level escalation.

## Interview prep — the BROKE-IT log

`NOTES.md` has the real debugging story: the untrusted-code-execution risk
analysis and production sandboxing plan, and a real bug the eval harness
caught (empty code not triggering the "stuck" escalation, and why) — what I
expected, what happened, how I found it, and what I changed. That log is the
answer to "tell me about an LLM app you shipped — what broke?"
