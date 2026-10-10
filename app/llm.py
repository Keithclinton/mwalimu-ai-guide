"""Single place for the Anthropic call. Both agent.py (drafting hints) and
tools.py (deriving rubric assertions) need it; living here instead of in
agent.py avoids a circular import between the two.
"""
import functools
import os

import anthropic


class LLMUnavailable(Exception):
    """The model couldn't give us usable text — an API error, a timeout, or a
    response with no text block. main.py turns this into a 503 so callers get
    a clean "try again" instead of a stack trace."""


@functools.cache
def _client() -> anthropic.Anthropic:
    # One client for the process, so connections are reused. The SDK default
    # timeout is 10 minutes — far too long for a hint someone is waiting on.
    # Retries cover 429s, 5xx and dropped connections.
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=30.0,
        max_retries=2,
    )


def call_llm(system: str, user: str) -> str:
    try:
        msg = _client().messages.create(
            model="claude-opus-5",
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIError as e:
        raise LLMUnavailable(str(e)) from e
    # content[0] isn't necessarily text: with thinking on, the first block is
    # a thinking block. Take the first text block instead.
    text = next((b.text for b in msg.content if b.type == "text"), "").strip()
    if not text:
        raise LLMUnavailable(f"no text in response (stop_reason={msg.stop_reason})")
    return text
