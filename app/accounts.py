"""Customers and API keys."""
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import ApiKey, Customer, utcnow

KEY_PREFIX = "mw_live_"
_SHOWN_PREFIX_LEN = len(KEY_PREFIX) + 8


@dataclass(frozen=True)
class Caller:
    """Who made a request. Plain values, so it's safe to use after the
    database session that loaded it has closed."""
    api_key_id: int
    customer_id: int
    rate_limit_per_minute: int


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_customer(db: Session, name: str, rate_limit_per_minute: int = 60) -> Customer:
    customer = Customer(name=name, rate_limit_per_minute=rate_limit_per_minute)
    db.add(customer)
    db.flush()
    return customer


def issue_key(db: Session, customer_id: int) -> str:
    """Create a key and return it. This is the only time the full key exists
    anywhere: only its hash is stored."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    db.add(ApiKey(customer_id=customer_id, key_hash=hash_key(key), prefix=key[:_SHOWN_PREFIX_LEN]))
    db.flush()
    return key


def revoke_key(db: Session, prefix: str) -> int:
    """Revoke the active key starting with `prefix`. Returns how many keys
    matched; anything other than 1 revokes nothing."""
    keys = db.scalars(
        select(ApiKey).where(ApiKey.prefix.startswith(prefix), ApiKey.revoked_at.is_(None))
    ).all()
    if len(keys) == 1:
        keys[0].revoked_at = utcnow()
    return len(keys)


def authenticate(db: Session, key: str) -> Optional[Caller]:
    row = db.execute(
        select(ApiKey.id, Customer.id, Customer.rate_limit_per_minute)
        .join(Customer, ApiKey.customer_id == Customer.id)
        .where(ApiKey.key_hash == hash_key(key), ApiKey.revoked_at.is_(None))
    ).first()
    return Caller(*row) if row else None
