import hashlib
import os
import stat

import pytest

from riskparse.evidence import archive
from riskparse.evidence.hashing import (
    file_type_for,
    fingerprint,
    is_sha256_hex,
    sha256_bytes,
    sha256_file,
)


def test_sha256_matches_hashlib(tmp_path):
    data = os.urandom(3 * 1024 * 1024 + 17)   # spans multiple chunks
    p = tmp_path / "a.bin"
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest() == sha256_bytes(data)


def test_identity_ignores_filename(tmp_path):
    a, b = tmp_path / "x.pdf", tmp_path / "completely_different_name.pdf"
    a.write_bytes(b"same bytes")
    b.write_bytes(b"same bytes")
    assert fingerprint(a).sha256 == fingerprint(b).sha256


def test_fingerprint_fields(tmp_path):
    p = tmp_path / "C.PDF"
    p.write_bytes(b"abc")
    fp = fingerprint(p)
    assert fp.size_bytes == 3 and fp.file_type == "PDF" and fp.modified_at_utc.tzinfo
    assert is_sha256_hex(fp.sha256)


def test_file_types():
    assert file_type_for("a.msg") == "MSG"
    assert file_type_for("a.eml") == "EML"
    assert file_type_for("a.unknown") == "OTHER"


def test_is_sha256_hex_rejects_bad():
    assert not is_sha256_hex("A" * 64)  # uppercase not canonical
    assert not is_sha256_hex("0" * 63)


def test_fingerprint_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        fingerprint(tmp_path / "missing.pdf")


def test_archive_store_is_content_addressed_and_idempotent(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"evidence")
    digest = sha256_file(src)
    t1 = archive.store(src, tmp_path / "arc", digest)
    t2 = archive.store(src, tmp_path / "arc", digest)
    assert t1 == t2 and t1.name == f"{digest}.pdf"
    assert not (t1.stat().st_mode & stat.S_IWUSR)  # read-only by policy


def test_archive_rejects_wrong_expected_hash(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"evidence")
    with pytest.raises(archive.ArchiveIntegrityError):
        archive.store(src, tmp_path / "arc", "0" * 64)
    assert not list((tmp_path / "arc").rglob("*.pdf"))


def test_archive_verify_detects_tampering(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"evidence")
    digest = sha256_file(src)
    t = archive.store(src, tmp_path / "arc", digest)
    os.chmod(t, stat.S_IRUSR | stat.S_IWUSR)
    t.write_bytes(b"tampered")
    with pytest.raises(archive.ArchiveIntegrityError):
        archive.verify(t, digest)
