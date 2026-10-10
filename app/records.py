"""Queries over hint_requests: session history, rate limiting, usage.

Configuration:
    MWALIMU_SESSION_HOURS   how far back a learner's hint history reaches,
                            default 24. Older hints stop counting towards
                            escalation, so a learner coming back to a
                            project days later starts gently again.
"""
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import tools
from app.db import HintRecord, utcnow

SESSION_HOURS = float(os.environ.get("MWALIMU_SESSION_HOURS", "24"))
# Only the most recent hints go into the prompt (and escalation caps at 3).
MAX_PRIOR_HINTS = 5


def prior_hints(db: Session, customer_id: int, project_id: str, learner_id: str) -> list[str]:
    """Hints this learner has already had on this project, oldest first.

    History stops at the last time their code passed: whatever they're
    stuck on now is a new problem, so escalation starts again from level 1."""
    rows = db.execute(
        select(HintRecord.hint, HintRecord.test_status)
        .where(
            HintRecord.customer_id == customer_id,
            HintRecord.project_id == project_id,
            HintRecord.learner_id == learner_id,
            HintRecord.status == "ok",
            HintRecord.created_at >= utcnow() - timedelta(hours=SESSION_HOURS),
        )
        .order_by(HintRecord.created_at.desc(), HintRecord.id.desc())
        .limit(MAX_PRIOR_HINTS)
    ).all()
    hints = []
    for hint, test_status in rows:
        if test_status == tools.PASSED:
            break
        hints.append(hint)
    return hints[::-1]


def requests_in_last_minute(db: Session, customer_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(HintRecord).where(
            HintRecord.customer_id == customer_id,
            HintRecord.created_at >= utcnow() - timedelta(minutes=1),
        )
    )


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """'2026-10' -> (start of October, start of November), in UTC."""
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def usage(db: Session, customer_id: int, month: str) -> dict:
    start, end = month_bounds(month)
    row = db.execute(
        select(
            func.count().filter(HintRecord.status == "ok"),
            func.count().filter(HintRecord.status != "ok"),
            func.coalesce(func.sum(case((HintRecord.leak_blocked, 1), else_=0)), 0),
            func.coalesce(func.sum(HintRecord.input_tokens), 0),
            func.coalesce(func.sum(HintRecord.output_tokens), 0),
        ).where(
            HintRecord.customer_id == customer_id,
            HintRecord.created_at >= start,
            HintRecord.created_at < end,
        )
    ).one()
    return {
        "month": month,
        "hints": row[0],
        "failed_requests": row[1],
        "leak_blocked": row[2],
        "input_tokens": row[3],
        "output_tokens": row[4],
    }
