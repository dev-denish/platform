"""Alembic environment. The database URL comes from the same typed Settings the app
uses (no duplicated connection config), and migrations run in online mode against the
pooled DSN."""
from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings

config = context.config

settings = get_settings()
_sqlalchemy_url = (
    f"postgresql+psycopg://{settings.db_user}:{settings.db_password}"
    f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
)
config.set_main_option("sqlalchemy.url", _sqlalchemy_url)

target_metadata = None  # raw-SQL migrations; no ORM metadata to autogenerate from


def run_migrations_offline() -> None:
    context.configure(url=_sqlalchemy_url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
