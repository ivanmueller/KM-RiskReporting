"""Alembic environment.

The database URL is never read from alembic.ini. It is resolved from riskparse
configuration (``RISKPARSE_CONFIG`` / ``--config``) through the guarded connection
layer, or an already-open connection is supplied programmatically.
"""

from __future__ import annotations

from alembic import context

from riskparse.config import load_settings
from riskparse.db.connection import create_db_engine
from riskparse.db.schema import metadata

config = context.config
target_metadata = metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    settings = load_settings(config.attributes.get("riskparse_config_path"))
    engine = create_db_engine(settings)
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit(
        "Offline (SQL script) mode is disabled until IT confirms the SQL engine; "
        "generated DDL must be reviewed for the confirmed dialect."
    )
run_migrations_online()
