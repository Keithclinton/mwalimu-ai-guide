"""Mwalimu: an AI project guide for coding learners.

For local development, put settings such as ANTHROPIC_API_KEY and
E2B_API_KEY in a .env file in the project root (see .env.example). It's
loaded here, so the server, migrations, the admin CLI, the evals and the
tests all see it. Variables already set in the environment take precedence
over the file. Production has no .env file: on Railway, set variables in the
service's settings.
"""
from pathlib import Path

from dotenv import load_dotenv

# An explicit path: without one, python-dotenv searches from the current
# directory in some cases (e.g. `python -c`), so whether .env was found
# would depend on where a command was run from.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
