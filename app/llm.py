"""Single place for the Anthropic call. Both agent.py (drafting hints) and
tools.py (deriving rubric assertions) need it; living here instead of in
agent.py avoids a circular import between the two.
"""
import os

from anthropic import Anthropic


def call_llm(system: str, user: str) -> str:
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-opus-5",
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text
