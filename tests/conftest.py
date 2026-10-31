"""Shared fixtures. All tests use synthetic data and the local SQLite test adapter."""

from __future__ import annotations

import pytest

from riskparse.config import settings_from_dict
from riskparse.db import migrate
from riskparse.db.connection import create_db_engine


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    monkeypatch.delenv("RISKPARSE_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings(tmp_path):
    return settings_from_dict({
        "environment": {"name": "test"},
        "database": {"backend": "sqlite", "sqlite_path": str(tmp_path / "t.db")},
        "paths": {"archive_root": str(tmp_path / "archive")},
    })


@pytest.fixture
def engine(settings):
    eng = create_db_engine(settings)
    migrate.upgrade(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(engine):
    with engine.begin() as c:
        yield c
