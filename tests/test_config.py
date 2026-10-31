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


# --- Regression tests: D-1 (type confusion bypass) and D-5 (unknown keys) -------------

def _dev(**db):
    base = {"backend": "postgresql", "host": "h", "database": "d"}
    base.update(db)
    return {"environment": {"name": "dev"}, "database": base}


@pytest.mark.parametrize("value", ["false", "true", "0", "no", 0, 1, None])
def test_d1_authorized_must_be_a_real_boolean(value):
    data = _dev()
    data["database"]["authorized"] = value
    with pytest.raises(ConfigError, match="authorized must be a TOML bool"):
        settings_from_dict(data)


@pytest.mark.parametrize("key,value", [("encrypt", "no"), ("trust_server_certificate", "yes"),
                                       ("port", "1433"), ("port", True), ("host", 5)])
def test_d1_other_fields_type_checked(key, value):
    with pytest.raises(ConfigError, match="must be a TOML"):
        settings_from_dict(_dev(**{key: value}))


def test_d1_quoted_false_in_toml_file_is_refused(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[environment]\nname="dev"\n[database]\nbackend="postgresql"\nhost="h"\n'
                 'database="d"\nauthorized="false"\n')
    with pytest.raises(ConfigError, match="authorized"):
        load_settings(p)


@pytest.mark.parametrize("port", [0, 70000, -1])
def test_port_range(port):
    with pytest.raises(ConfigError, match="port"):
        settings_from_dict(_dev(port=port))


@pytest.mark.parametrize("key,value", [("host", "svc:pw@sql01"), ("odbc_driver", "X;PWD=pw"),
                                       ("database", "risk;Trusted_Connection=no"),
                                       ("host", "postgresql://u:p@h")])
def test_connection_attribute_injection_rejected(key, value):
    with pytest.raises(ConfigError, match="could embed credentials"):
        settings_from_dict(_dev(**{key: value}))


def test_env_indirection_must_be_a_variable_name():
    with pytest.raises(ConfigError, match="environment variable NAME"):
        settings_from_dict(_dev(password_env="hunter2!"))


@pytest.mark.parametrize("key", ["db_password", "Password", "api_token", "client_secret"])
def test_credential_like_keys_rejected_anywhere(key):
    with pytest.raises(ConfigError, match="forbidden"):
        settings_from_dict({"paths": {key: "x"}})


@pytest.mark.parametrize("data", [{"paths": {"bogus": 1}}, {"environment": {"nme": "test"}},
                                  {"password": "x"}, {"databse": {}}])
def test_d5_unknown_keys_and_sections_raise_config_error(data):
    with pytest.raises(ConfigError):
        settings_from_dict(data)


@pytest.mark.parametrize("name", ["Prod", "PROD", " prod", "production"])
def test_prod_lookalike_environment_names_rejected(name):
    with pytest.raises(ConfigError, match="environment.name"):
        settings_from_dict({"environment": {"name": name}})


def test_cli_unknown_key_exits_cleanly(tmp_path, capsys):
    from riskparse.cli import main
    p = tmp_path / "s.toml"
    p.write_text('[paths]\nbogus = 1\n')
    assert main(["--config", str(p), "config-check"]) == 3
    assert "unknown" in capsys.readouterr().err
