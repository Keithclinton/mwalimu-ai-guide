"""FastAPI entrypoint. This layer is done — it validates input and calls the
agent. You shouldn't need to change much here; the work is in agent.py."""
from fastapi import FastAPI
from pydantic import BaseModel

from app.agent import run_agent

app = FastAPI(title="Mwalimu", description="AI project-guide for coding learners")


class HintRequest(BaseModel):
    brief: str          # what the learner is trying to build
    code: str           # the learner's current code
    rubric: str         # what "correct" looks like
    prior_hints: list[str] = []   # hints already given this session


class HintResponse(BaseModel):
    hint: str
    hint_level: int     # 1 = gentle nudge ... 3 = concrete step
    solved_leak_blocked: bool


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/hint", response_model=HintResponse)
def hint(req: HintRequest):
    result = run_agent(
        brief=req.brief,
        code=req.code,
        rubric=req.rubric,
        prior_hints=req.prior_hints,
    )
    return HintResponse(**result)
