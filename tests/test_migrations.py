"""Migration chain tests (portable features only, run on the SQLite test adapter)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select

from riskparse.db import migrate
from riskparse.db.schema import active_publication, metadata


def test_single_head():
    heads = ScriptDirectory.from_config(migrate.alembic_config()).get_heads()
    assert heads == ["0001_initial"]


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
    assert migrate.current_revision(eng) == "0001_initial"
    eng.dispose()


def test_upgrade_is_idempotent(engine):
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == "0001_initial"


def test_migration_file_is_frozen():
    """The migration must not import the live schema module."""
    src = (migrate.MIGRATIONS_DIR / "versions" / "0001_initial.py").read_text()
    assert "riskparse" not in src.split('"""', 2)[2]
