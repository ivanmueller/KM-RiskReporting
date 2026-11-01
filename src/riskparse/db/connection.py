"""Configuration-driven database connection layer.

The SQL product used by the company has **not** been confirmed (game plan 1.1 / 3.1).
This module therefore:

* builds connection URLs from validated :class:`~riskparse.config.Settings` only,
* never accepts a raw connection string or a password from a config file,
* refuses server connections unless the configuration is marked ``authorized``,
* refuses ``prod`` entirely during Phase 1,
* supports SQLite purely as a local test adapter for synthetic data.

The ``mssql`` and ``postgresql`` URL builders are *candidates* and are unverified
against any real instance. Do not treat their presence as confirmation of the engine.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL

from riskparse.config import ConfigError, Settings, validate


class ConnectionNotAuthorized(ConfigError):
    """Raised when a connection would breach the Phase 1 access rules."""


def build_url(settings: Settings) -> URL:
    """Return the SQLAlchemy URL for ``settings`` without connecting.

    Settings are re-validated here because callers can construct ``Settings`` directly,
    bypassing the TOML loader; this is the public connection boundary.
    """
    validate(settings)
    db = settings.database
    if db.backend == "sqlite":
        if db.sqlite_path == ":memory:":
            return URL.create("sqlite", database=":memory:")
        return URL.create("sqlite", database=str(Path(db.sqlite_path)))

    _assert_server_allowed(settings)
    username = password = None
    if db.authentication == "env_password":
        username = os.environ.get(db.username_env)
        password = os.environ.get(db.password_env)
        if not username or not password:
            raise ConfigError(
                f"authentication=env_password requires environment variables "
                f"{db.username_env} and {db.password_env} to be set by IT-approved means"
            )

    if db.backend == "mssql":
        query: dict[str, str] = {
            "driver": db.odbc_driver or "",
            "Encrypt": "yes" if db.encrypt else "no",
            "TrustServerCertificate": "yes" if db.trust_server_certificate else "no",
        }
        if db.authentication == "integrated":
            query["Trusted_Connection"] = "yes"
        return URL.create(
            "mssql+pyodbc",
            username=username,
            password=password,
            host=db.host,
            port=db.port,
            database=db.database,
            query=query,
        )
    if db.backend == "postgresql":
        query = {"sslmode": "require" if db.encrypt else "prefer"}
        return URL.create(
            "postgresql+psycopg",
            username=username,
            password=password,
            host=db.host,
            port=db.port,
            database=db.database,
            query=query,
        )
    raise ConfigError(f"unsupported backend {db.backend}")  # pragma: no cover


def _assert_server_allowed(settings: Settings) -> None:
    if settings.environment == "prod":
        raise ConnectionNotAuthorized(
            "Phase 1 does not connect to production. Production access requires the "
            "IT/security readiness gate (game plan 17, validation sign-off)."
        )
    if not settings.database.authorized:
        raise ConnectionNotAuthorized(
            f"backend '{settings.database.backend}' is configured but not authorized. "
            "Set database.authorized = true only in an IT-approved configuration."
        )


def _enable_sqlite_safety(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()


def create_db_engine(settings: Settings, *, echo: bool = False) -> Engine:
    """Create an engine for ``settings`` (enforcing all access rules)."""
    url = build_url(settings)
    if settings.database.backend == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, echo=echo, future=True)
        _enable_sqlite_safety(engine)
        return engine
    connect_args: dict[str, object] = {}
    engine = create_engine(url, echo=echo, pool_pre_ping=True, connect_args=connect_args)
    if settings.database.schema:
        engine = engine.execution_options(
            schema_translate_map={None: settings.database.schema}
        )
    return engine


def describe_url(settings: Settings) -> str:
    """Printable URL with any password masked."""
    try:
        return build_url(settings).render_as_string(hide_password=True)
    except ConfigError as exc:
        return f"<not connectable: {exc}>"
