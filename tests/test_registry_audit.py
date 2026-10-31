import os
import stat

from sqlalchemy import func, select, text

from riskparse import audit
from riskparse.db.schema import audit_events, document_observations, source_documents
from riskparse.evidence.registry import (
    DUPLICATE,
    NEW,
    REVISION,
    register_document,
    verify_archive,
)


def _reg(conn, path, tmp_path, **kw):
    return register_document(conn, path, actor="tester", source_channel="TEST",
                             archive_root=tmp_path / "arc", is_synthetic=True, **kw)


def test_new_duplicate_and_revision(conn, tmp_path):
    a = tmp_path / "in1" / "confirm.pdf"
    a.parent.mkdir()
    a.write_bytes(b"version one")
    dup = tmp_path / "in2" / "renamed.pdf"
    dup.parent.mkdir()
    dup.write_bytes(b"version one")
    rev = tmp_path / "in2" / "CONFIRM.pdf"   # same name (case-insensitive), new bytes
    rev.write_bytes(b"version two")

    r1 = _reg(conn, a, tmp_path)
    r2 = _reg(conn, dup, tmp_path)
    r3 = _reg(conn, rev, tmp_path)

    assert r1.outcome == NEW
    assert r2.outcome == DUPLICATE and r2.document_id == r1.document_id
    assert r3.outcome == REVISION and r3.revision_of_document_id == r1.document_id
    assert r3.document_id != r1.document_id
    assert conn.execute(select(func.count()).select_from(source_documents)).scalar() == 2
    # every sighting is retained, including the duplicate's other filename
    names = {r.observed_filename for r in conn.execute(select(document_observations))}
    assert names == {"confirm.pdf", "renamed.pdf", "CONFIRM.pdf"}
    actions = [r.action for r in conn.execute(select(audit_events.c.action))]
    assert actions == ["DOCUMENT_REGISTERED", "DOCUMENT_DUPLICATE_OBSERVED",
                       "DOCUMENT_REVISION_DETECTED"]
    # revision did not overwrite the original archived bytes
    assert verify_archive(conn) == []


def test_document_id_not_derived_from_filename(conn, tmp_path):
    p = tmp_path / "2026-10-14_ABC_Energy_PHYS_SELL_TC12345.pdf"
    p.write_bytes(b"x")
    r = _reg(conn, p, tmp_path)
    assert r.document_id.startswith("DOC-") and "TC12345" not in r.document_id


def test_verify_archive_detects_altered_bytes(conn, tmp_path):
    p = tmp_path / "c.pdf"
    p.write_bytes(b"original")
    r = _reg(conn, p, tmp_path)
    os.chmod(r.archive_uri, stat.S_IRUSR | stat.S_IWUSR)
    with open(r.archive_uri, "wb") as fh:
        fh.write(b"altered")
    problems = verify_archive(conn)
    assert problems and problems[0][0] == r.document_id


def test_audit_chain_verifies_and_detects_tampering(conn):
    for i in range(3):
        audit.record(conn, actor="a", action="TEST", entity_type="x", entity_id=str(i),
                     after={"i": i})
    assert audit.verify_chain(conn).ok
    conn.execute(text("UPDATE audit_events SET after_json = :v WHERE event_id = 2"),
                 {"v": '{"i":99}'})
    v = audit.verify_chain(conn)
    assert not v.ok and v.first_bad_event_id == 2


def test_audit_chain_detects_deleted_event(conn):
    for i in range(3):
        audit.record(conn, actor="a", action="TEST", entity_type="x", entity_id=str(i))
    conn.execute(text("DELETE FROM audit_events WHERE event_id = 2"))
    v = audit.verify_chain(conn)
    assert not v.ok and v.first_bad_event_id == 3


def test_audit_requires_independent_approver(conn):
    import pytest
    with pytest.raises(ValueError, match="separation"):
        audit.record(conn, actor="same", approver="same", action="T", entity_type="x",
                     entity_id="1")
