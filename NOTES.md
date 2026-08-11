# BROKE-IT log

Every time something breaks, write: what I expected / what happened / how I found out / what I changed.
This is your interview gold. Fill it as you go.

## Day 1
- 

## Day 4 — eval harness

**Blocked on live runs:** the Anthropic account still has a $0 credit balance
(same error as Day 1's first `/hint` call), so I could not run
`evals/run_evals.py` against the real model yet — every call fails on
billing before it reaches the model. What's below came from a **mechanical
dry-run** of the harness with `agent.call_llm`/`tools.call_llm` monkeypatched
to canned responses, to verify the harness and scenario logic don't have
bugs of their own. That's a genuine finding, just not the live-model "did it
solve" run this eval suite exists for — that still needs to happen once
credits are added, and may surface different failures than this one.

- **What I expected:** the `empty_code` scenario (learner has written
  nothing yet) to hit `decide_level`'s "stuck on fundamentals" bump and come
  back at hint_level >= 2, same as the syntax-error scenario.
- **What happened:** it came back at level 1, as if the code ran cleanly.
- **How I found it:** dry-run of `evals/run_evals.py` with a mocked
  `tools.call_llm` returning a trivial `assert True, "placeholder"` (a
  stand-in for the assertion-derivation call producing something that
  doesn't reference the target function). `tools.run_tests` was concatenating
  that onto `code=""` and running it — `assert True` on its own doesn't
  raise, so the subprocess exited 0, `test_output` never contained "errored",
  and `decide_level` had no signal to bump the level.
- **Root cause:** `_derive_assertions` only received `code` and `rubric`,
  never `brief`. When `code` is empty there's no `def` to regex out a
  function name from, so the assertion-writing prompt fell back to the
  useless placeholder `"FUNCTION TO TEST: the function"` — it had no
  authoritative source for the name the rubric was implicitly describing.
  Whether the real model would still guess the right name from the rubric
  alone (plausible for `is_prime`, less plausible for a less suggestive
  rubric) isn't something I can verify without credits, and I didn't want to
  rely on the model bailing me out of a design gap.
- **What I changed:** two fixes, not one:
  1. `tools.run_tests` now short-circuits on empty code with a deterministic
     `"Code errored: no code has been written yet."` — doesn't depend on the
     LLM-derived assertions accidentally referencing an undefined function.
  2. `_derive_assertions` now also takes `brief` and puts it in the prompt as
     `TASK: ...`, and falls back to parsing a function name out of the brief
     (`\b(\w+)\s*\([^)]*\)`) when the code has no `def` to extract from. This
     matters beyond the empty-code case too — any time the learner's function
     name doesn't match what the brief asked for, the assertions should test
     against the *intended* name, not whatever (if anything) is on the page.
  `agent.assess` now passes `state["brief"]` through to `run_tests` to make
  this possible.

**Next:** re-run `python -m evals.run_evals` for real once credits are
added — this dry-run only proves the harness plumbing is sound, not that the
model actually behaves (leak-avoidance, prompt-injection resistance, and the
"already correct" congratulate-don't-invent-a-fix case are all still
unverified against the live model).

## Security notes — untrusted code execution (tools.run_tests)

`run_tests` executes two things it doesn't control: the learner's submitted
code, and a handful of `assert` statements an LLM generated from the rubric.
Both run as a plain `subprocess.run([sys.executable, "-c", program], timeout=5)`
on the same host as the server.

**What the timeout-guarded subprocess actually buys you:** it stops the
*server process* from hanging on an infinite loop, and `subprocess.run`
kills the child on timeout so it doesn't linger. That's it.

**What it does NOT buy you** — this is not a sandbox:
- No filesystem isolation. The code runs as the same OS user as the server
  and can read/write anything that user can.
- No network isolation. It can make outbound requests (exfiltrate data,
  call other services, hit internal endpoints) unless the host network
  itself blocks it.
- No CPU/memory ceiling. A process that allocates memory or busy-loops
  without tripping the wall-clock timeout can still degrade the host before
  5 seconds is up.
- No syscall restriction. It can spawn further processes, import arbitrary
  modules (`os`, `subprocess`, `ctypes`, `socket`), etc.
- The LLM-derived assertions are a second untrusted-code source, not just
  the learner's code — a prompt-injected rubric or a model slip could put
  something unwanted into the assert lines just as easily as into the
  learner's submission. It gets no less scrutiny than the code it's
  appended to.

**How I'd sandbox this for production** (the interview answer):
1. Run each execution in a locked-down container per request (gVisor or
   Firecracker microVM, not just a bare subprocess) — no network egress by
   default, read-only root filesystem, tmpfs for scratch space.
2. Non-root, unprivileged user inside the container; drop all capabilities
   it doesn't need.
3. Hard resource limits via cgroups — CPU quota, memory ceiling, max PIDs —
   enforced by the OS, not just a Python-side timeout.
4. Defense in depth on top of the sandbox, not instead of it: an AST-level
   static check that rejects `import os`, `import subprocess`, `import
   socket`, `eval`/`exec`, etc. before execution, so obviously hostile code
   never even reaches the runtime.
5. Short-lived, single-use execution environment — destroy and recreate per
   run rather than reusing a warm container, so nothing persists between
   learners.
6. Rate-limit executions per user/session — a sandbox stops one run from
   doing damage, not a user from hammering the endpoint.

None of this is implemented here — Day 1 is the subprocess+timeout floor,
which is fine for a local learning tool but not for running strangers'
code in production.
