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
   FastAPI  POST /v1/hint                       (app/main.py)
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
  `assert` statements in an isolated E2B sandbox, and records what
  actually failed.
- **`decide_level`** picks hint level 1–3: escalates with how many hints
  the learner has already burned through, plus a bump if the code doesn't
  even run (a different, more fundamental kind of stuck than a logic bug).
- **`draft_hint`** calls Claude with a level-specific system prompt.
- **`leak_check`** rejects hints that read like a full solution and, up to
  a retry budget, loops back to `draft_hint` with the level demoted —
  rather than either shipping a leaked answer or looping forever.

## Why it's built this way (the interesting decisions)

- **Context engineering.** Each step sees only what it needs —
  `context.for_assertions` gets the brief, rubric, and code (for the
  signature), but not prior hints or test output; `context.for_hint` gets
  the brief, the test result, and hints already given (so it doesn't repeat
  itself), plus the learner's code only at levels 2–3, where pointing at a
  line needs it. Anything the learner controls goes in wrapped in tags the
  system prompt marks as data, not instructions. Deciding what's in scope
  at each step, and why, is the actual point of the project.
  (`app/context.py`)
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

`tools.run_tests` executes untrusted code: the learner's submission, plus
`assert` statements an LLM wrote (validated with `ast` first).

- **In production** each run gets a fresh [E2B](https://e2b.dev) microVM with
  its own kernel, no internet access and none of the server's environment
  variables, killed as soon as the run ends. Nothing the code does can reach
  the API server, its secrets, or the network.
- **If E2B is unavailable**, the code isn't run at all: the hint is written
  without test results (`test_status: "unchecked"`) and the reason is
  logged. It never falls back to running code on the server.
- **Locally** (no `E2B_API_KEY`), code runs in a subprocess with a 5-second
  timeout and a stripped-down environment. That's for development only; the
  server logs a warning on startup when it's in this mode.

Either way, the stderr that flows back into the hint prompt is capped.

## Using the API

Every request needs a customer API key:
`Authorization: Bearer mw_live_...`.

### `POST /v1/hint`

```bash
curl -X POST https://<your-domain>/v1/hint \
  -H "Authorization: Bearer mw_live_..." -H "content-type: application/json" \
  -d '{"brief": "Write is_prime(n)",
       "code": "def is_prime(n):\n    return True",
       "rubric": "handles n<2, even numbers, and non-primes",
       "project_id": "primes-101",
       "learner_id": "student-42"}'
```

| Field | Limit | |
|---|---|---|
| `brief` | 1–4,000 chars | What the learner is building. |
| `code` | up to 20,000 chars | Their current code. Not stored. |
| `rubric` | 1–4,000 chars | What "correct" looks like. |
| `project_id`, `learner_id` | 1–128 chars | Your own identifiers. Hint history is kept per customer, project and learner. |

Response:

```json
{"hint": "What should is_prime return for n = 1?", "hint_level": 1,
 "solved_leak_blocked": false, "test_status": "failed"}
```

- `hint_level` goes 1 → 3 as the learner asks again about the same
  project. History reaches back `MWALIMU_SESSION_HOURS` (24h) and starts
  over once their code passes.
- `test_status` is one of `passed`, `unchecked`, `failed`, `errored`,
  `timeout` or `empty`.

| Status | Meaning |
|---|---|
| 401 | Missing, invalid or revoked API key. |
| 422 | Invalid body (missing field, over a limit). |
| 429 | Over the per-minute rate limit; retry after `Retry-After` seconds. |
| 503 | The model is unavailable; safe to retry. The reason is in the server logs. |

### `GET /v1/usage?month=YYYY-MM`

Hints, failed requests, leak blocks and tokens for the calling customer,
for one month (default: current month, UTC).

## Deploy on Railway

1. Create a Railway project from this GitHub repo. Railway builds the
   `Dockerfile`.
2. Add a **PostgreSQL** database to the project.
3. In the API service's variables, set:
   - `DATABASE_URL` = `${{ Postgres.DATABASE_URL }}`
   - `ANTHROPIC_API_KEY` = your key
   - `E2B_API_KEY` = your E2B key (from the E2B dashboard). **Required for
     customers**: without it, learner code runs inside the API container.
   - optionally anything from [Configuration](#configuration)
4. In the service settings, set the healthcheck path to `/health` and
   generate a public domain.
5. Deploy. Database migrations run automatically when the container starts.
   Check the deploy logs for `learner code runs in E2B sandboxes`; a warning
   about code running on this server means `E2B_API_KEY` isn't set.
6. Create your first customer. Enable public networking on the Postgres
   service, copy its `DATABASE_PUBLIC_URL`, and run the admin command from
   your machine:

   ```bash
   docker build -t mwalimu .
   docker run --rm -e DATABASE_URL="<DATABASE_PUBLIC_URL>" mwalimu \
     python -m app.admin create-customer "Acme Academy" --rate-limit 60
   ```

   The API key is printed once and never stored. Other commands:
   `create-key`, `revoke-key`, `list-customers`, `usage` (see `app/admin.py`).

## Run it locally

```bash
git clone <this-repo-url>
cd mwalimu-ai-guide

python -m venv .venv             # Python 3.10+ (3.12 matches Docker and CI)
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1

pip install -r requirements.txt

cp .env.example .env             # then fill in ANTHROPIC_API_KEY and E2B_API_KEY

alembic upgrade head             # creates mwalimu.db (SQLite) without DATABASE_URL
python -m app.admin create-customer "Local testing"   # prints an API key
uvicorn app.main:app --reload
```

Then call `localhost:8000/v1/hint` as above with that key. Send the same
request again to see the level escalate.

### Configuration

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. |
| `DATABASE_URL` | `sqlite:///mwalimu.db` | Postgres in production. |
| `MWALIMU_MODEL` | `claude-opus-5-5` | Model for hints and rubric checks. |
| `MWALIMU_MAX_TOKENS` | `8000` | Includes thinking, so leave headroom beyond the hint length. |
| `MWALIMU_LLM_TIMEOUT` | `60` | Seconds per model call attempt (2 retries). |
| `MWALIMU_SESSION_HOURS` | `24` | How far back a learner's hint history counts. |
| `E2B_API_KEY` | — | Runs learner code in E2B sandboxes. Required in production. |
| `MWALIMU_SANDBOX` | `e2b` if `E2B_API_KEY` is set, else `local` | `local` runs code on the server; development only. |
| `PORT` | `8000` | Set by Railway automatically (Docker only). |
| `WEB_CONCURRENCY` | `2` | Uvicorn worker processes (Docker only). |

### Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests mock the model and E2B, and cost nothing. They run on every
push via GitHub Actions. To check the real sandbox (a few seconds of E2B
time):

```bash
E2B_API_KEY=e2b_... pytest tests/test_sandbox_live.py
```

### Run the evals

```bash
python -m evals.run_evals --runs 3      # pass rate per scenario, plus the leak rate
python -m evals.run_evals --show-hints  # also print every hint
python -m evals.run_evals --no-judge    # skip the LLM judge (cheaper)
```

These call the real model and cost money. Each hint is checked by
banned-fragment and code-block checks, then by an independent LLM judge
(`evals/judge.py`, `MWALIMU_JUDGE_MODEL`, default `claude-sonnet-5-5`) that
catches pseudocode and step-by-step walkthroughs. The model's output varies,
so read pass rates over several runs, not a single pass. The **leak rate**
(hints a learner would have seen that gave the answer away) is the number
to track. In GitHub, run them from the Actions tab ("Live evals"); they need
an `ANTHROPIC_API_KEY` repository secret.

### Dependencies

`requirements.in` lists the direct dependencies; `requirements.txt` is the
fully pinned set, resolved on Python 3.12 (the Docker image). To change a
dependency, edit `requirements.in` and regenerate:

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim \
  sh -c "pip install -q -r requirements.in && pip freeze" > requirements.txt
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
5. `app/main.py` — the HTTP API: auth, input limits, rate limiting, and
   recording each request.
6. `app/db.py`, `app/accounts.py`, `app/records.py` — customers, API keys,
   and the hint history used for sessions, rate limits and metering.
   Schema changes go in `migrations/` (Alembic).
7. `app/admin.py` — admin commands for customers and keys.
8. `evals/run_evals.py` + `evals/scenarios.json` — 8 scenarios, adversarial
   ones included, checking both leak-avoidance and level escalation.

## Interview prep — the BROKE-IT log

`NOTES.md` has the real debugging story: the untrusted-code-execution risk
analysis and production sandboxing plan, and a real bug the eval harness
caught (empty code not triggering the "stuck" escalation, and why) — what I
expected, what happened, how I found it, and what I changed. That log is the
answer to "tell me about an LLM app you shipped — what broke?"
