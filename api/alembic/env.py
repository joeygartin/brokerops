import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url() -> str:
    # Migrations are schema management: they run as the owner role via
    # MIGRATION_DATABASE_URL when a hardened deploy splits the DB roles (BOP-013).
    # Local/compose leaves it unset and falls back to the single DATABASE_URL role.
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get(
        "DATABASE_URL", "postgresql://brokerops:brokerops@localhost:5432/brokerops_demo"
    )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
