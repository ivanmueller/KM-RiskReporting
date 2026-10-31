import pytest

from riskparse.config import ConfigError, load_settings, settings_from_dict


def test_defaults_are_local_sqlite():
    s = load_settings()
    assert s.environment == "local"
    assert s.database.backend == "sqlite"
    assert s.database.authorized is False


def test_loads_from_env_var(tmp_path, monkeypatch):
    p = tmp_path / "s.toml"
    p.write_text('[environment]\nname="test"\n[database]\nbackend="sqlite"\n'
                 'sqlite_path="x.db"\n')
    monkeypatch.setenv("RISKPARSE_CONFIG", str(p))
    s = load_settings()
    assert s.environment == "test" and s.source_file == str(p)


def test_missing_explicit_file_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "nope.toml")


@pytest.mark.parametrize("key", ["password", "PWD", "connection_string", "url"])
def test_credentials_in_config_are_rejected(key):
    with pytest.raises(ConfigError, match="forbidden"):
        settings_from_dict({"database": {"backend": "sqlite", key: "x"}})


def test_unknown_database_key_rejected():
    with pytest.raises(ConfigError, match="unknown"):
        settings_from_dict({"database": {"hostname": "x"}})


def test_unsupported_backend_rejected():
    with pytest.raises(ConfigError, match="not supported"):
        settings_from_dict({"database": {"backend": "oracle"}})


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_sqlite_only_for_local_and_test(env):
    with pytest.raises(ConfigError, match="test adapter"):
        settings_from_dict({"environment": {"name": env}, "database": {"backend": "sqlite"}})


def test_server_backend_requires_host_and_database():
    with pytest.raises(ConfigError, match="host"):
        settings_from_dict({"environment": {"name": "dev"},
                            "database": {"backend": "postgresql"}})


def test_mssql_requires_driver():
    with pytest.raises(ConfigError, match="odbc_driver"):
        settings_from_dict({"environment": {"name": "dev"},
                            "database": {"backend": "mssql", "host": "h", "database": "d"}})


def test_redacted_contains_no_secret_values(monkeypatch):
    monkeypatch.setenv("RISKPARSE_DB_PASSWORD", "s3cr3t")
    s = settings_from_dict({"environment": {"name": "dev"},
                            "database": {"backend": "postgresql", "host": "h", "database": "d",
                                         "authentication": "env_password"}})
    assert "s3cr3t" not in repr(s.redacted())
