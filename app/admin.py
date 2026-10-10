"""Admin commands. Run against the database in DATABASE_URL:

    python -m app.admin create-customer "Acme Academy" [--rate-limit 60]
    python -m app.admin create-key CUSTOMER_ID
    python -m app.admin revoke-key KEY_PREFIX
    python -m app.admin list-customers
    python -m app.admin usage CUSTOMER_ID [--month 2026-10]

New keys are printed once and never stored; copy them straight to the
customer.
"""
import argparse
import json
import sys

from sqlalchemy import select

from app import accounts, records
from app.db import ApiKey, Customer, SessionLocal, utcnow


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.admin")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-customer", help="create a customer and their first API key")
    p.add_argument("name")
    p.add_argument("--rate-limit", type=int, default=60, help="hint requests per minute")

    p = sub.add_parser("create-key", help="issue another API key for a customer")
    p.add_argument("customer_id", type=int)

    p = sub.add_parser("revoke-key", help="revoke a key by its prefix (as shown by list-customers)")
    p.add_argument("prefix")

    sub.add_parser("list-customers", help="customers and their active key prefixes")

    p = sub.add_parser("usage", help="a customer's usage for a month")
    p.add_argument("customer_id", type=int)
    p.add_argument("--month", default=None, help="YYYY-MM, default this month (UTC)")

    args = parser.parse_args(argv)
    with SessionLocal() as db:
        if args.command == "create-customer":
            customer = accounts.create_customer(db, args.name, args.rate_limit)
            key = accounts.issue_key(db, customer.id)
            db.commit()
            print(f"customer_id: {customer.id}\napi_key:     {key}\n(shown once; it is not stored)")
        elif args.command == "create-key":
            if db.get(Customer, args.customer_id) is None:
                print(f"no customer with id {args.customer_id}", file=sys.stderr)
                return 1
            key = accounts.issue_key(db, args.customer_id)
            db.commit()
            print(f"api_key: {key}\n(shown once; it is not stored)")
        elif args.command == "revoke-key":
            matched = accounts.revoke_key(db, args.prefix)
            if matched != 1:
                print(f"{matched} active keys match {args.prefix!r}; nothing revoked", file=sys.stderr)
                return 1
            db.commit()
            print(f"revoked {args.prefix}")
        elif args.command == "list-customers":
            for customer in db.scalars(select(Customer).order_by(Customer.id)):
                prefixes = db.scalars(
                    select(ApiKey.prefix).where(ApiKey.customer_id == customer.id, ApiKey.revoked_at.is_(None))
                ).all()
                print(f"{customer.id}\t{customer.name}\t{customer.rate_limit_per_minute}/min\t"
                      f"active keys: {', '.join(prefixes) or '-'}")
        elif args.command == "usage":
            month = args.month or utcnow().strftime("%Y-%m")
            print(json.dumps(records.usage(db, args.customer_id, month), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
