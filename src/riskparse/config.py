"""Configuration loading and validation.

Configuration is TOML (stdlib ``tomllib``). The file is located, in order, by:

1. an explicit path passed by the caller (CLI ``--config``),
2. the ``RISKPARSE_CONFIG`` environment variable,
3. ``config/settings.local.toml`` relative to the working directory,
4. built-in defaults (local SQLite test adapter).

Safety rules enforced here (game plan sections 3.1, 13.1, 13.2):

* No credentials in configuration files - a ``password`` key is rejected outright.
* SQLite is a *local test adapter* and is only permitted for ``local``/``test``.
* Server backends require ``authorized = true`` (set from IT-approved config).
* Phase 1 never connects to an environment named ``prod``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_VAR = "RISKPARSE_CONFIG"
DEFAULT_LOCAL_PATH = Path("config/settings.local.toml")

SUPPORTED_BACKENDS = ("sqlite", "mssql", "postgresql")
SERVER_BACKENDS = ("mssql", "postgresql")
ENVIRONMENTS = ("local", "test", "dev", "prod")
SQLITE_ENVIRONMENTS = ("local", "test")
AUTH_MODES = ("integrated", "env_password")

_FORBIDDEN_KEYS = {"password", "pwd", "secret", "connection_string", "url"}


class ConfigError(ValueError):
    """Raised when configuration is missing, unsafe or inconsistent."""


@dataclass(frozen=True)
class DatabaseConfig:
    backend: str = "sqlite"
    sqlite_path: str = "var/riskparse_local.db"
    host: str | None = None
    port: int | None = None
    database: str | None = None
    schema: str | None = None
    odbc_driver: str | None = None
    authentication: str = "integrated"
    username_env: str = "RISKPARSE_DB_USER"
    password_env: str = "RISKPARSE_DB_PASSWORD"
    encrypt: bool = True
    trust_server_certificate: bool = False
    authorized: bool = False

    @property
    def is_server(self) -> bool:
        return self.backend in SERVER_BACKENDS


@dataclass(frozen=True)
class PathsConfig:
    archive_root: str = "var/archive"


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    source_file: str | None = None

    def redacted(self) -> dict[str, Any]:
        """A printable view with no secret material (there should be none anyway)."""
        db = self.database
        return {
            "source_file": self.source_file,
            "environment": self.environment,
            "database": {
                "backend": db.backend,
                "sqlite_path": db.sqlite_path if db.backend == "sqlite" else None,
                "host": db.host,
                "port": db.port,
                "database": db.database,
                "schema": db.schema,
                "odbc_driver": db.odbc_driver,
                "authentication": db.authentication if db.is_server else None,
                "username_env": db.username_env if db.is_server else None,
                "password_env": db.password_env if db.is_server else None,
                "authorized": db.authorized,
            },
            "paths": {"archive_root": self.paths.archive_root},
        }


def _check_forbidden(section: str, data: dict[str, Any]) -> None:
    bad = sorted(k for k in data if k.lower() in _FORBIDDEN_KEYS)
    if bad:
        raise ConfigError(
            f"[{section}] contains forbidden key(s) {bad}. Credentials and full connection "
            "strings must not be stored in configuration files; use integrated auth or "
            "the *_env indirection approved by IT."
        )


def _build(data: dict[str, Any], source: str | None) -> Settings:
    env_section = data.get("environment", {}) or {}
    db_section = data.get("database", {}) or {}
    paths_section = data.get("paths", {}) or {}
    for name, sec in (("environment", env_section), ("database", db_section),
                      ("paths", paths_section)):
        if not isinstance(sec, dict):
            raise ConfigError(f"[{name}] must be a table")
        _check_forbidden(name, sec)

    known_db = set(DatabaseConfig.__dataclass_fields__)
    unknown = sorted(set(db_section) - known_db)
    if unknown:
        raise ConfigError(f"[database] has unknown key(s): {unknown}")

    env_name = str(env_section.get("name", "local"))
    db = DatabaseConfig(**db_section)
    paths = PathsConfig(**paths_section)
    settings = Settings(environment=env_name, database=db, paths=paths, source_file=source)
    validate(settings)
    return settings


def validate(settings: Settings) -> None:
    db = settings.database
    if settings.environment not in ENVIRONMENTS:
        raise ConfigError(f"environment.name must be one of {ENVIRONMENTS}")
    if db.backend not in SUPPORTED_BACKENDS:
        raise ConfigError(
            f"database.backend '{db.backend}' is not supported; expected one of "
            f"{SUPPORTED_BACKENDS}. Other engines need an adapter after IT confirmation."
        )
    if db.backend == "sqlite":
        if settings.environment not in SQLITE_ENVIRONMENTS:
            raise ConfigError(
                "SQLite is a local test adapter for synthetic data only and cannot be used "
                f"for environment '{settings.environment}'."
            )
        if not db.sqlite_path:
            raise ConfigError("database.sqlite_path is required for sqlite")
        return
    # server backends
    if db.authentication not in AUTH_MODES:
        raise ConfigError(f"database.authentication must be one of {AUTH_MODES}")
    for required in ("host", "database"):
        if not getattr(db, required):
            raise ConfigError(f"database.{required} is required for backend '{db.backend}'")
    if db.backend == "mssql" and not db.odbc_driver:
        raise ConfigError("database.odbc_driver is required for mssql (confirm with IT)")


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from the first available source (see module docstring)."""
    candidate: Path | None = None
    if path is not None:
        candidate = Path(path)
        if not candidate.is_file():
            raise ConfigError(f"configuration file not found: {candidate}")
    elif os.environ.get(ENV_VAR):
        candidate = Path(os.environ[ENV_VAR])
        if not candidate.is_file():
            raise ConfigError(f"{ENV_VAR} points to a missing file: {candidate}")
    elif DEFAULT_LOCAL_PATH.is_file():
        candidate = DEFAULT_LOCAL_PATH

    if candidate is None:
        return _build({}, None)
    with candidate.open("rb") as fh:
        data = tomllib.load(fh)
    return _build(data, str(candidate))


def settings_from_dict(data: dict[str, Any]) -> Settings:
    """Build settings from an in-memory mapping (used by tests and the demo)."""
    return _build(data, None)
