"""Programmatic migration helpers (used by the CLI and tests)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(connection: Connection | None = None,
                   config_path: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    if config_path is not None:
        cfg.attributes["riskparse_config_path"] = config_path
    return cfg


class MigrationIntegrityError(RuntimeError):
    """A migration would leave rows that break a foreign key."""


def run_guarded(engine: Engine, fn: Callable[[Connection], None]) -> None:
    """Run ``fn`` in one transaction, with SQLite foreign-key handling.

    SQLite cannot alter constraints in place, so Alembic batch mode rebuilds tables
    (create new, copy, DROP old, rename). With ``PRAGMA foreign_keys=ON`` dropping a
    referenced table fails once rows exist. Per the SQLite documentation, enforcement
    is switched off for the migration, ``PRAGMA foreign_key_check`` must come back
    empty **before** commit, and enforcement is switched back on afterwards.
    Other engines run ``fn`` in a plain transaction.
    """
    if engine.dialect.name != "sqlite":
        with engine.begin() as conn:
            fn(conn)
        return
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")   # no-op inside a transaction
        conn.commit()
        if conn.exec_driver_sql("PRAGMA foreign_keys").scalar() != 0:
            raise MigrationIntegrityError("could not disable SQLite foreign keys")
        conn.commit()
        try:
            with conn.begin():
                fn(conn)
                broken = conn.exec_driver_sql("PRAGMA foreign_key_check").all()
                if broken:
                    raise MigrationIntegrityError(
                        f"migration would leave {len(broken)} foreign-key violation(s), e.g. "
                        f"{broken[:3]}; rolled back")
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


def upgrade(engine: Engine, revision: str = "head") -> None:
    run_guarded(engine, lambda conn: command.upgrade(alembic_config(conn), revision))


def downgrade(engine: Engine, revision: str) -> None:
    run_guarded(engine, lambda conn: command.downgrade(alembic_config(conn), revision))


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()
