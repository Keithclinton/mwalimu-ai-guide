"""Single place for the Anthropic call. Both agent.py (drafting hints) and
tools.py (deriving rubric assertions) need it; living here instead of in
agent.py avoids a circular import between the two.

Configuration (environment variables):
    ANTHROPIC_API_KEY     required
    MWALIMU_MODEL         default claude-opus-5-5
    MWALIMU_MAX_TOKENS    default 8000. Thinking is on for current models and
                          counts against this, so it needs headroom beyond
                          the length of the hint itself.
    MWALIMU_LLM_TIMEOUT   seconds per attempt, default 60
"""
import functools
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

import anthropic

MODEL = os.environ.get("MWALIMU_MODEL", "claude-opus-5-5")
MAX_TOKENS = int(os.environ.get("MWALIMU_MAX_TOKENS", "8000"))
TIMEOUT_SECONDS = float(os.environ.get("MWALIMU_LLM_TIMEOUT", "60"))

# Models that accept server-side refusal fallbacks in the "default" form. If a
# safety classifier declines a request (a false positive is possible on any
# input), the API re-runs it on a fallback model instead of returning nothing.
_FALLBACK_MODELS = frozenset({
    "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5",
})


class LLMUnavailable(Exception):
    """The model couldn't give us usable text — an API error, a timeout, a
    refusal, or a response with no text block. main.py turns this into a 503
    so callers get a clean "try again" instead of a stack trace."""


# Token totals for the request being handled. A hint request makes several
# model calls (rubric checks, then one or more hint drafts) from inside the
# agent graph; this collects all of them without threading a counter through
# every function in between.
_usage: ContextVar[Optional[dict]] = ContextVar("mwalimu_usage", default=None)


@contextmanager
def track_usage() -> Iterator[dict]:
    """Collect token usage from every call_llm made inside the block."""
    totals = {"input_tokens": 0, "output_tokens": 0}
    token = _usage.set(totals)
    try:
        yield totals
    finally:
        _usage.reset(token)


@functools.cache
def _client() -> anthropic.Anthropic:
    # One client for the process, so connections are reused. The SDK default
    # timeout is 10 minutes — far too long for a hint someone is waiting on.
    # Retries cover 429s, 5xx and dropped connections.
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=TIMEOUT_SECONDS,
        max_retries=2,
    )


def call_llm(system: str, user: str) -> str:
    params = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    try:
        if MODEL in _FALLBACK_MODELS:
            msg = _client().beta.messages.create(
                **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        else:
            msg = _client().messages.create(**params)
    except anthropic.APIError as e:
        raise LLMUnavailable(str(e)) from e
    totals = _usage.get()
    if totals is not None and msg.usage is not None:
        # Billed even when the reply turns out to be unusable.
        totals["input_tokens"] += msg.usage.input_tokens or 0
        totals["output_tokens"] += msg.usage.output_tokens or 0
    if msg.stop_reason == "refusal":
        raise LLMUnavailable("model declined the request")
    # content[0] isn't necessarily text: with thinking on, the first block is
    # a thinking block. Take the first text block instead.
    text = next((b.text for b in msg.content if b.type == "text"), "").strip()
    if not text:
        raise LLMUnavailable(f"no text in response (stop_reason={msg.stop_reason})")
    return text
