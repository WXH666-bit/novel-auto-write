"""Alembic environment for SQLite desktop and MySQL production."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from app import models  # noqa: F401  (register mappings)
from app.config import DATABASE_URL
from app.db import Base
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    connectable = supplied_connection or engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    def migrate(connection) -> None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()

    if supplied_connection is not None:
        migrate(supplied_connection)
    else:
        with connectable.connect() as connection:
            migrate(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
