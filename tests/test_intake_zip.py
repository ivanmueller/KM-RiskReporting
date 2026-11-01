"""Safe ZIP intake (roadmap B8). All archives are built synthetically in tmp_path."""

import csv
import hashlib
import stat
import warnings
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from riskparse.db.schema import source_documents
from riskparse.evidence.intake_zip import (
    ZipIntakeError,
    manifest_rows,
    register_intake,
    safe_extract,
    write_manifest,
)
from riskparse.inventory.template import SHEETS
from riskparse.inventory.workbook import ERROR, validate_rows


def _zip(path, members, *, compression=zipfile.ZIP_DEFLATED):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on duplicate names - intended here
        with zipfile.ZipFile(path, "w", compression=compression) as zf:
            for m in members:
                if isinstance(m, zipfile.ZipInfo):
                    zf.writestr(m, b"x")
                else:
                    name, data = m
                    zf.writestr(name, data)
    return path


def _kinds(result):
    return {(f.kind, f.member) for f in result.findings}


def test_normal_extraction_hashes_every_member(tmp_path):
    z = _zip(tmp_path / "oct.zip", [("Physical/a.pdf", b"one"), ("Financial/b.xlsx", b"two")])
    zip_bytes = z.read_bytes()
    r = safe_extract(z, tmp_path / "out")
    assert r.zip_sha256 == hashlib.sha256(zip_bytes).hexdigest()
    assert z.read_bytes() == zip_bytes                       # original untouched
    got = {m.member: m.sha256 for m in r.members}
    assert got == {"Physical/a.pdf": hashlib.sha256(b"one").hexdigest(),
                   "Financial/b.xlsx": hashlib.sha256(b"two").hexdigest()}
    for m in r.members:
        assert m.path.read_bytes() in (b"one", b"two")
        assert not (m.path.stat().st_mode & stat.S_IWUSR)    # extracted copies read-only
    assert r.findings == [] and r.skipped == []


@pytest.mark.parametrize("name", ["../evil.pdf", "a/../../evil.pdf", "/etc/evil", "C:/evil.pdf",
                                  "C:evil.pdf", "..\\evil.pdf", "\\\\server\\share\\x.pdf",
                                  "a/CON.pdf", "a/b?.pdf", "trail. ", "a/b:stream"])
def test_unsafe_paths_refused_and_nothing_written_outside(tmp_path, name):
    out = tmp_path / "out"
    r = safe_extract(_zip(tmp_path / "z.zip", [(name, b"bad"), ("ok.pdf", b"ok")]), out)
    assert ("UNSAFE_PATH", name) in _kinds(r)
    assert name in r.skipped
    assert [m.member for m in r.members] == ["ok.pdf"]
    written = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    assert written == {"z.zip", "ok.pdf"}


def test_symlink_entries_refused(tmp_path):
    info = zipfile.ZipInfo("link.pdf")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    r = safe_extract(_zip(tmp_path / "z.zip", [info]), tmp_path / "out")
    assert ("SYMLINK", "link.pdf") in _kinds(r) and not r.members


def test_encrypted_entries_refused(tmp_path):
    # zipfile cannot write encrypted members, so set the "encrypted" flag bit directly
    # in the local (offset 6) and central-directory (offset 8) headers.
    z = _zip(tmp_path / "z.zip", [("secret.pdf", b"x")], compression=zipfile.ZIP_STORED)
    data = bytearray(z.read_bytes())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.index(sig)
        data[i + off] |= 0x1
    z.write_bytes(bytes(data))
    r = safe_extract(z, tmp_path / "out")
    assert ("ENCRYPTED", "secret.pdf") in _kinds(r) and not r.members


def test_duplicate_member_names_are_kept_not_overwritten(tmp_path):
    r = safe_extract(_zip(tmp_path / "z.zip", [("a.pdf", b"first"), ("a.pdf", b"second")]),
                     tmp_path / "out")
    contents = sorted(m.path.read_bytes() for m in r.members)
    assert contents == [b"first", b"second"]
    assert any(k == "DUPLICATE_NAME" for k, _ in _kinds(r))


def test_case_collision_reported(tmp_path):
    r = safe_extract(_zip(tmp_path / "z.zip", [("A.pdf", b"1"), ("a.pdf", b"2")]),
                     tmp_path / "out")
    assert len(r.members) == 2
    assert any(k == "CASE_COLLISION" for k, _ in _kinds(r))


def test_nested_archive_macro_and_empty_flagged(tmp_path):
    r = safe_extract(_zip(tmp_path / "z.zip", [("inner.zip", b"PK"), ("book.xlsm", b"m"),
                                               ("empty.pdf", b"")]), tmp_path / "out")
    kinds = {k for k, _ in _kinds(r)}
    assert {"NESTED_ARCHIVE", "MACRO_ENABLED", "EMPTY_FILE"} <= kinds
    assert len(r.members) == 3                                   # flagged, not dropped


def test_declared_size_limit(tmp_path):
    z = _zip(tmp_path / "z.zip", [("a.pdf", b"x" * 1000)])
    with pytest.raises(ZipIntakeError, match="exceeds limit"):
        safe_extract(z, tmp_path / "out", max_total_bytes=999)


def test_compression_ratio_limit(tmp_path):
    z = _zip(tmp_path / "bomb.zip", [("zeros.pdf", b"\0" * (11 * 1024 * 1024))])
    with pytest.raises(ZipIntakeError, match="zip bomb"):
        safe_extract(z, tmp_path / "out")


def test_member_count_limit(tmp_path):
    z = _zip(tmp_path / "z.zip", [(f"{i}.pdf", b"x") for i in range(5)])
    with pytest.raises(ZipIntakeError, match="members"):
        safe_extract(z, tmp_path / "out", max_members=4)


def test_refuses_non_empty_destination_and_non_zip(tmp_path):
    z = _zip(tmp_path / "z.zip", [("a.pdf", b"x")])
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "keep.txt").write_text("keep")
    with pytest.raises(ZipIntakeError, match="empty"):
        safe_extract(z, tmp_path / "busy")
    fake = tmp_path / "fake.zip"
    fake.write_bytes(b"not a zip")
    with pytest.raises(ZipIntakeError, match="not a ZIP"):
        safe_extract(fake, tmp_path / "out2")


def test_manifest_is_valid_inventory_and_never_overwritten(tmp_path):
    r = safe_extract(_zip(tmp_path / "z.zip", [("Physical/a.pdf", b"1"), ("../x.pdf", b"2")]),
                     tmp_path / "out")
    rows = manifest_rows(r, inventoried_by="analyst", inventoried_date="2026-11-02")
    assert rows[0]["file_type"] == "ZIP" and rows[0]["doc_inventory_id"] == "INV-ZIP-0001"
    assert all(row["parent_doc_inventory_id"] == "INV-ZIP-0001" for row in rows[1:])
    excepted = [row for row in rows if row["inventory_status"] == "EXCEPTED"]
    assert len(excepted) == 1 and excepted[0]["exception_reason"]
    out = write_manifest(rows, tmp_path / "m.csv")
    with pytest.raises(FileExistsError):
        write_manifest(rows, out)
    with out.open(newline="", encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    data = {s.name: [] for s in SHEETS}
    data["Contract_Documents"] = csv_rows
    errs = [i for i in validate_rows(data) if i.severity == ERROR and
            i.sheet == "Contract_Documents"]
    assert errs == []


def test_register_links_members_to_zip(conn, tmp_path):
    r = safe_extract(_zip(tmp_path / "z.zip", [("a.pdf", b"1"), ("b/c.pdf", b"2")]),
                     tmp_path / "out")
    zip_reg, regs = register_intake(conn, r, actor="analyst", source_channel="EMAIL_ZIP",
                                    archive_root=tmp_path / "arc")
    docs = {d.document_id: d for d in conn.execute(select(source_documents))}
    assert docs[zip_reg.document_id].file_type == "ZIP"
    assert all(docs[x.document_id].parent_document_id == zip_reg.document_id for x in regs)
    from riskparse.db.schema import document_observations
    obs = conn.execute(select(document_observations).where(
        document_observations.c.container_document_id == zip_reg.document_id)).all()
    assert sorted(o.observed_path.split("!/")[1] for o in obs) == ["a.pdf", "b/c.pdf"]
    assert all(Path(docs[x.document_id].original_path).is_file() for x in regs)   # real files


def test_repeated_member_keeps_every_container(conn, tmp_path):
    """Same bytes in two archives: one document, two sightings, each with its own ZIP."""
    from riskparse.db.schema import document_observations
    r1 = safe_extract(_zip(tmp_path / "one.zip", [("a.pdf", b"same")]), tmp_path / "o1")
    r2 = safe_extract(_zip(tmp_path / "two.zip", [("x/a.pdf", b"same")]), tmp_path / "o2")
    z1, m1 = register_intake(conn, r1, actor="analyst", source_channel="Z", archive_root=None)
    z2, m2 = register_intake(conn, r2, actor="analyst", source_channel="Z", archive_root=None)
    assert m1[0].document_id == m2[0].document_id and m2[0].outcome == "DUPLICATE"
    containers = {o.container_document_id for o in conn.execute(select(document_observations)
        .where(document_observations.c.document_id == m1[0].document_id))}
    assert containers == {z1.document_id, z2.document_id}


def test_register_detects_member_changed_after_extraction(conn, tmp_path):
    r = safe_extract(_zip(tmp_path / "z.zip", [("a.pdf", b"1")]), tmp_path / "out")
    p = r.members[0].path
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    p.write_bytes(b"changed")
    with pytest.raises(ZipIntakeError, match="changed after extraction"):
        register_intake(conn, r, actor="analyst", source_channel="EMAIL_ZIP", archive_root=None)


def test_cli_intake_zip(tmp_path, capsys):
    from riskparse.cli import main
    z = _zip(tmp_path / "z.zip", [("a.pdf", b"1")])
    cfg = tmp_path / "s.toml"
    cfg.write_text(f'[environment]\nname="test"\n[database]\nbackend="sqlite"\n'
                   f'sqlite_path="{(tmp_path / "c.db").as_posix()}"\n'
                   f'[paths]\narchive_root="{(tmp_path / "arc").as_posix()}"\n')
    assert main(["--config", str(cfg), "db-upgrade"]) == 0
    assert main(["--config", str(cfg), "intake-zip", str(z), "--extract-to",
                 str(tmp_path / "out"), "--manifest", str(tmp_path / "m.csv"), "--register",
                 "--by", "analyst", "--received-at", "2026-11-02T09:15-07:00"]) == 0
    out = capsys.readouterr().out
    assert "registered ZIP as DOC-" in out
    # second run must refuse: destination no longer empty, manifest exists
    assert main(["intake-zip", str(z), "--extract-to", str(tmp_path / "out"),
                 "--manifest", str(tmp_path / "m2.csv")]) == 4


def test_relative_paths_work_and_manifest_paths_are_absolute(tmp_path):
    # conftest chdir()s into tmp_path, so these are relative paths
    _zip(tmp_path / "rel.zip", [("Physical/a.pdf", b"1")])
    r = safe_extract("rel.zip", "extracted")
    rows = manifest_rows(r)
    assert rows[1]["folder_hint"] == "Physical"
    assert all(Path(row["unc_path"].split("!/")[0]).is_absolute() for row in rows)
