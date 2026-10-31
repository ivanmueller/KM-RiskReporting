import pytest
from sqlalchemy import text

from riskparse.config import ConfigError, settings_from_dict
from riskparse.db.connection import (
    ConnectionNotAuthorized,
    build_url,
    create_db_engine,
    describe_url,
)


def _server(backend="mssql", env="dev", authorized=True, **db):
    base = {"backend": backend, "host": "sql.example.invalid", "database": "risk_dev",
            "authorized": authorized}
    if backend == "mssql":
        base["odbc_driver"] = "ODBC Driver 18 for SQL Server"
    base.update(db)
    return settings_from_dict({"environment": {"name": env}, "database": base})


def test_sqlite_engine_enforces_foreign_keys(settings):
    eng = create_db_engine(settings)
    with eng.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    eng.dispose()


def test_unauthorized_server_config_refused():
    with pytest.raises(ConnectionNotAuthorized, match="not authorized"):
        build_url(_server(authorized=False))


def test_prod_is_always_refused_in_phase1():
    with pytest.raises(ConnectionNotAuthorized, match="production"):
        build_url(_server(env="prod"))


def test_mssql_integrated_url_has_no_credentials():
    url = build_url(_server())
    assert url.drivername == "mssql+pyodbc"
    assert url.username is None and url.password is None
    assert url.query["Trusted_Connection"] == "yes"
    assert url.query["Encrypt"] == "yes"


def test_env_password_requires_env_vars(monkeypatch):
    monkeypatch.delenv("RISKPARSE_DB_USER", raising=False)
    monkeypatch.delenv("RISKPARSE_DB_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="environment variables"):
        build_url(_server(backend="postgresql", authentication="env_password"))


def test_env_password_is_masked(monkeypatch):
    monkeypatch.setenv("RISKPARSE_DB_USER", "svc")
    monkeypatch.setenv("RISKPARSE_DB_PASSWORD", "s3cr3t")
    s = _server(backend="postgresql", authentication="env_password")
    assert "s3cr3t" not in describe_url(s)
    assert build_url(s).password == "s3cr3t"


def test_describe_url_reports_refusal_without_raising():
    assert "not connectable" in describe_url(_server(authorized=False))
