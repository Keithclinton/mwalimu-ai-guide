"""Database: customers, their API keys, and a record of every hint request.

One table, hint_requests, does three jobs:
  - session history (#13): the hints a learner has already had on a project
  - rate limiting (#5): how many requests a customer made in the last minute
  - metering (#14): hints and tokens per customer, for billing

Configuration:
    DATABASE_URL   Postgres in production (on Railway, reference the database
                   service's DATABASE_URL). Defaults to a local SQLite file.

Schema changes go through Alembic (migrations/); the container runs
`alembic upgrade head` on start.
"""
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///mwalimu.db")
    # Hosts hand out postgres:// or postgresql://; SQLAlchemy needs the
    # driver named explicitly to use psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


engine = create_engine(database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    rate_limit_per_minute: Mapped[int] = mapped_column(default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    # Only a SHA-256 of the key is stored; the key itself is shown once, when
    # it's created. Keys are 32 random bytes, so an unsalted hash is enough.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The first characters of the key, so admins and customers can tell keys
    # apart without anyone storing the whole thing.
    prefix: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)


class HintRecord(Base):
    __tablename__ = "hint_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"))
    # Opaque strings chosen by the customer. The learner's code is
    # deliberately not stored; only the hint we gave back.
    project_id: Mapped[str] = mapped_column(String(128))
    learner_id: Mapped[str] = mapped_column(String(128))
    # pending -> ok | unavailable | error. Written as pending before the
    # model is called, so in-flight requests count towards the rate limit.
    status: Mapped[str] = mapped_column(String(16), default="pending")
    test_status: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    hint: Mapped[Optional[str]] = mapped_column(Text, default=None)
    hint_level: Mapped[Optional[int]] = mapped_column(default=None)
    leak_blocked: Mapped[bool] = mapped_column(default=False)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_hint_requests_session", "customer_id", "project_id", "learner_id", "created_at"),
        Index("ix_hint_requests_customer_time", "customer_id", "created_at"),
    )
