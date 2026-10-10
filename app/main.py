"""FastAPI entrypoint: authentication, input limits, rate limiting, and
recording each request. The tutoring logic is in agent.py."""
import logging
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import accounts, records
from app.accounts import Caller
from app.agent import run_agent
from app.db import HintRecord, SessionLocal, utcnow
from app.llm import LLMUnavailable, track_usage

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Mwalimu", description="AI project-guide for coding learners")

# Generous for a learner's exercise, small enough that the API can't be used
# as a general-purpose model proxy.
MAX_BRIEF_CHARS = 4_000
MAX_CODE_CHARS = 20_000
MAX_RUBRIC_CHARS = 4_000
MAX_ID_CHARS = 128


@app.exception_handler(LLMUnavailable)
def llm_unavailable(request: Request, exc: LLMUnavailable):
    # The caller gets a generic message; the reason (bad key, outage,
    # refusal) goes to the logs, or a misconfiguration looks like an outage.
    logger.warning("model unavailable: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "The hint service is temporarily unavailable. Please try again."},
    )


def require_api_key(authorization: Optional[str] = Header(default=None)) -> Caller:
    unauthorized = {"WWW-Authenticate": "Bearer"}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing API key. Send 'Authorization: Bearer <key>'.", headers=unauthorized)
    with SessionLocal() as db:
        caller = accounts.authenticate(db, authorization[len("Bearer "):].strip())
    if caller is None:
        raise HTTPException(401, "Invalid or revoked API key.", headers=unauthorized)
    return caller


class HintRequest(BaseModel):
    brief: str = Field(min_length=1, max_length=MAX_BRIEF_CHARS)    # what the learner is trying to build
    code: str = Field(max_length=MAX_CODE_CHARS)                     # the learner's current code
    rubric: str = Field(min_length=1, max_length=MAX_RUBRIC_CHARS)  # what "correct" looks like
    # Chosen by the customer. Hint history is kept per (customer, project,
    # learner), so the same learner_id under two customers never mixes.
    project_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    learner_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)


class HintResponse(BaseModel):
    hint: str
    hint_level: int     # 1 = gentle nudge ... 3 = concrete step
    solved_leak_blocked: bool
    test_status: str    # passed | unchecked | failed | errored | timeout | empty


class UsageResponse(BaseModel):
    month: str
    hints: int
    failed_requests: int
    leak_blocked: int
    input_tokens: int
    output_tokens: int


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/hint", response_model=HintResponse)
def hint(req: HintRequest, caller: Caller = Depends(require_api_key)):
    with SessionLocal() as db:
        if records.requests_in_last_minute(db, caller.customer_id) >= caller.rate_limit_per_minute:
            raise HTTPException(429, "Rate limit exceeded. Try again in a minute.", headers={"Retry-After": "60"})
        prior = records.prior_hints(db, caller.customer_id, req.project_id, req.learner_id)
        # Recorded before the model runs, so requests still in flight count
        # towards the rate limit.
        record = HintRecord(
            customer_id=caller.customer_id, api_key_id=caller.api_key_id,
            project_id=req.project_id, learner_id=req.learner_id,
        )
        db.add(record)
        db.commit()

    outcome = {"status": "error"}
    with track_usage() as tokens:
        try:
            result = run_agent(brief=req.brief, code=req.code, rubric=req.rubric, prior_hints=prior)
            outcome = {
                "status": "ok",
                "hint": result["hint"],
                "hint_level": result["hint_level"],
                "leak_blocked": result["solved_leak_blocked"],
                "test_status": result["test_status"],
            }
        except LLMUnavailable:
            outcome = {"status": "unavailable"}
            raise
        finally:
            with SessionLocal() as db:
                row = db.get(HintRecord, record.id)
                for field, value in {**outcome, **tokens}.items():
                    setattr(row, field, value)
                db.commit()
    return HintResponse(**result)


@app.get("/v1/usage", response_model=UsageResponse)
def usage(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM, default this month (UTC)"),
    caller: Caller = Depends(require_api_key),
):
    with SessionLocal() as db:
        return records.usage(db, caller.customer_id, month or utcnow().strftime("%Y-%m"))
