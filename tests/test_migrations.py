"""Migration chain tests (portable features only, run on the SQLite test adapter)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select

from riskparse.db import migrate
from riskparse.db.schema import active_publication, metadata

HEAD = "0003_document_extraction"


def test_single_head():
    heads = ScriptDirectory.from_config(migrate.alembic_config()).get_heads()
    assert heads == [HEAD]


def _checks(conn) -> dict[str, dict[str, str]]:
    insp = inspect(conn)
    norm = lambda t: " ".join(t.replace("(", " ( ").replace(")", " ) ").split())  # noqa: E731
    return {t: {c["name"]: norm(c["sqltext"]) for c in insp.get_check_constraints(t)}
            for t in insp.get_table_names() if t != "alembic_version"}


def test_migrated_check_constraints_match_metadata_exactly(engine, tmp_path):
    """Alembic's compare_metadata ignores CHECK constraints on SQLite, so compare text."""
    ref = create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    metadata.create_all(ref)
    with engine.connect() as a, ref.connect() as b:
        migrated, expected = _checks(a), _checks(b)
    ref.dispose()
    assert migrated == expected


def test_0002_preserves_data_and_other_constraints(tmp_path):
    from datetime import UTC, datetime

    import pytest
    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError

    from riskparse.db.schema import publication_runs
    eng = create_engine(f"sqlite:///{tmp_path / 'd.db'}")
    migrate.upgrade(eng, "0001_initial")
    now = datetime(2026, 10, 31, tzinfo=UTC)
    row = dict(publication_id="P1", reporting_month="2026-10", reporting_cutoff_utc=now,
               reporting_timezone="America/Edmonton", source_inventory_digest="0" * 64,
               code_version="c", config_version="c", created_by="a", created_at_utc=now)
    with eng.begin() as c:
        c.execute(insert(publication_runs).values(**row))
    migrate.upgrade(eng)
    with eng.begin() as c:
        assert c.execute(select(publication_runs.c.publication_id)).scalar() == "P1"
    with pytest.raises(IntegrityError), eng.begin() as c:   # month check survived rebuild
        c.execute(insert(publication_runs).values(**{**row, "publication_id": "P2",
                                                     "reporting_month": "Oct-26"}))
    with pytest.raises(IntegrityError), eng.begin() as c:   # new case-insensitive SoD
        c.execute(insert(publication_runs).values(**{**row, "publication_id": "P3",
                                                     "created_by": "Pat", "approved_by": "pat",
                                                     "approved_at_utc": now}))
    eng.dispose()


def test_upgrade_creates_all_tables(engine):
    assert migrate.current_revision(engine) == migrate.head_revision()
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(metadata.tables)


def test_migrated_schema_matches_metadata(engine):
    with engine.connect() as c:
        mc = MigrationContext.configure(c, opts={"compare_type": True})
        diffs = compare_metadata(mc, metadata)
    assert diffs == []


def test_active_pointer_seeded_and_empty(engine):
    with engine.connect() as c:
        rows = c.execute(select(active_publication)).all()
    assert len(rows) == 1 and rows[0].slot == "ACTIVE" and rows[0].publication_id is None


def test_downgrade_to_base_and_reupgrade(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    migrate.upgrade(eng)
    migrate.downgrade(eng, "base")
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}
    migrate.upgrade(eng)
    assert migrate.current_revision(eng) == HEAD
    eng.dispose()


def test_upgrade_is_idempotent(engine):
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == HEAD


def test_migration_files_are_frozen():
    """Migrations must not import the live schema module."""
    files = sorted((migrate.MIGRATIONS_DIR / "versions").glob("0*.py"))
    assert [f.name for f in files] == ["0001_initial.py", "0002_sod_case_and_zip.py",
                                      "0003_document_extraction.py"]
    for f in files:
        assert "riskparse" not in f.read_text().split('"""', 2)[2], f.name


def _fk_engine(tmp_path, name="fk.db"):
    from riskparse.config import settings_from_dict
    from riskparse.db.connection import create_db_engine
    return create_db_engine(settings_from_dict({
        "environment": {"name": "test"},
        "database": {"backend": "sqlite", "sqlite_path": str(tmp_path / name)}}))


def test_upgrade_with_linked_rows_under_enforced_foreign_keys(tmp_path):
    """Batch rebuild of a referenced table must not fail once rows exist (0002 bug)."""
    from sqlalchemy import func, text

    from riskparse.db.schema import document_observations
    from riskparse.evidence.registry import register_document
    eng = _fk_engine(tmp_path)
    migrate.upgrade(eng, "0001_initial")
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    with eng.begin() as c:
        register_document(c, f, actor="t", source_channel="T")
    migrate.upgrade(eng)
    migrate.downgrade(eng, "0001_initial")
    migrate.upgrade(eng)
    with eng.connect() as c:
        assert c.execute(select(func.count()).select_from(document_observations)).scalar() == 1
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1   # restored
        assert c.execute(text("PRAGMA foreign_key_check")).all() == []
    eng.dispose()


def test_guard_rolls_back_when_foreign_keys_would_break(tmp_path):
    import pytest
    from sqlalchemy import text
    eng = _fk_engine(tmp_path)
    migrate.upgrade(eng)

    def corrupt(conn):
        conn.execute(text("INSERT INTO document_observations (document_id, observed_path, "
                          "observed_filename, observed_at_utc) VALUES ('NOPE', 'p', 'f', "
                          "'2026-10-31 00:00:00')"))
    with pytest.raises(migrate.MigrationIntegrityError):
        migrate.run_guarded(eng, corrupt)
    with eng.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM document_observations")).scalar() == 0
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    eng.dispose()


def test_alembic_cli_path_uses_the_same_guard(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from riskparse.evidence.registry import register_document
    db = tmp_path / "cli.db"
    cfg_file = tmp_path / "s.toml"
    cfg_file.write_text(f'[environment]\nname="test"\n[database]\nbackend="sqlite"\n'
                        f'sqlite_path="{db.as_posix()}"\n')
    monkeypatch.setenv("RISKPARSE_CONFIG", str(cfg_file))
    cfg = Config()
    cfg.set_main_option("script_location", str(migrate.MIGRATIONS_DIR))
    command.upgrade(cfg, "0001_initial")
    eng = _fk_engine(tmp_path, "cli.db")
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    with eng.begin() as c:
        register_document(c, f, actor="t", source_channel="T")
    eng.dispose()
    command.upgrade(cfg, "head")
    eng = _fk_engine(tmp_path, "cli.db")
    assert migrate.current_revision(eng) == HEAD
    eng.dispose()
