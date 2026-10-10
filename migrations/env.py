"""Alembic environment. Run `alembic upgrade head` to bring the database in
DATABASE_URL up to date; the container does this on every start."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from app.db import Base, database_url

config = context.config
if config.config_file_name is not None:
    # Leave loggers that already exist alone, e.g. when migrations run
    # in-process from the test suite.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Every replica runs migrations when it starts. On Postgres this lock makes
# the others wait for the first instead of racing it.
_MIGRATION_LOCK_ID = 7_265_204


def run_migrations_online() -> None:
    # Tests point at a throwaway database through sqlalchemy.url.
    url = config.get_main_option("sqlalchemy.url") or database_url()
    engine = create_engine(url)
    with engine.connect() as connection:
        locked = connection.dialect.name == "postgresql"
        if locked:
            connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _MIGRATION_LOCK_ID})
            connection.commit()
        try:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if locked:
                connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _MIGRATION_LOCK_ID})
                connection.commit()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations aren't supported; run against a database.")
run_migrations_online()
