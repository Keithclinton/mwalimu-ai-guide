from alembic import command
from alembic.config import Config


def test_migrations_match_the_models(tmp_path):
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'migrations.db'}")
    command.upgrade(cfg, "head")
    # Fails if a model changed without a matching migration.
    command.check(cfg)
    command.downgrade(cfg, "base")
