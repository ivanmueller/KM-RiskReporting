"""Profiling a contracts folder and storing extractions in SQL (synthetic data only)."""

import csv
import io
import os
import stat

import olefile
import pytest
from sqlalchemy import func, select

from doc_fixtures import build_cfb, build_msg, build_pdf, build_xlsx
from riskparse import audit
from riskparse.db.schema import document_extractions, document_segments, source_documents
from riskparse.evidence.registry import EvidenceIntegrityError, register_document
from riskparse.extract.profile import folder_class, format_summary, profile_folder, write_profile
from riskparse.extract.store import extract_all_pending, extract_document


@pytest.mark.parametrize("n_streams,big", [(1, False), (3, False), (4, False), (7, False),
                                            (9, True), (40, True)])
def test_fixture_writer_is_strictly_valid(n_streams, big):
    streams = {f"s{i}": bytes([i]) * (i * 37 + 1) for i in range(n_streams)}
    if big:
        streams["store/big"] = b"B" * 9000
    ole = olefile.OleFileIO(io.BytesIO(build_cfb(streams)),
                            raise_defects=olefile.DEFECT_INCORRECT)
    for path, data in streams.items():
        assert ole.openstream(path).read() == data
    ole.close()


def _contracts_tree(root):
    """Mirror the user's layout:
    Risk Program/1. Contracts/{Evergreen,Financial,Physical} Contracts"""
    base = root / "Risk Program" / "1. Contracts"
    phys, fin, ever = (base / "Physical Contracts", base / "Financial Contracts",
                       base / "Evergreen Contracts")
    for d in (phys, fin, ever):
        d.mkdir(parents=True)
    (phys / "SYN_phys_confirm.pdf").write_bytes(build_pdf([["SYNTHETIC physical confirm text"]]))
    (phys / "SYN_scanned.pdf").write_bytes(build_pdf([[]]))
    (fin / "SYN_swap.xlsx").write_bytes(build_xlsx())
    (ever / "SYN_evergreen_email.msg").write_bytes(build_msg(
        attachments=[("SYN_evergreen_agreement.pdf",
                      build_pdf([["SYNTHETIC evergreen agreement stated value clause"]]), 1)]))
    (phys / "~$SYN_lock.xlsx").write_bytes(b"lock")
    return base


def test_folder_class_is_a_hint_from_path():
    from pathlib import Path
    assert folder_class(Path("Physical Contracts/a.pdf")) == "PHYSICAL"
    assert folder_class(Path("Evergreen Contracts/x/a.pdf")) == "EVERGREEN"
    assert folder_class(Path("misc/a.pdf")) == "UNKNOWN"
    assert folder_class(Path("Physical/Financial/a.pdf")).startswith("AMBIGUOUS")


def test_profile_counts_every_document_including_attachments(tmp_path):
    base = _contracts_tree(tmp_path)
    before = sorted((p, p.read_bytes()) for p in base.rglob("*") if p.is_file())
    result = profile_folder(base)
    rows = {r["relative_path"]: r for r in result.rows}
    assert len(result.rows) == 5                       # 4 files + 1 attachment, lock file skipped
    att = rows["Evergreen Contracts/SYN_evergreen_email.msg!/SYN_evergreen_agreement.pdf"]
    assert att["parent"] == "Evergreen Contracts/SYN_evergreen_email.msg"
    assert att["file_type"] == "PDF" and att["status"] == "TEXT_EXTRACTED"
    assert att["folder_class"] == "EVERGREEN"
    assert rows["Physical Contracts/SYN_scanned.pdf"]["status"] == "NEEDS_OCR"
    assert rows["Financial Contracts/SYN_swap.xlsx"]["hidden_sheets"] == "Workings (hidden)"
    assert rows["Evergreen Contracts/SYN_evergreen_email.msg"]["attachment_types"] == "PDF"
    summary = format_summary(result)
    assert "4 file(s) on disk, 1 email attachment(s), 5 document(s)" in summary
    after = sorted((p, p.read_bytes()) for p in base.rglob("*") if p.is_file())
    assert before == after                               # read-only


def test_profile_output_has_no_document_content_and_never_overwrites(tmp_path):
    base = _contracts_tree(tmp_path)
    out = write_profile(profile_folder(base), tmp_path / "profile.csv")
    text = out.read_text(encoding="utf-8")
    assert "SYNTHETIC physical confirm text" not in text      # no body / cell / line text
    assert "SYNTHETIC confirmation" not in text               # no email subject
    with pytest.raises(FileExistsError):
        write_profile(profile_folder(base), out)
    with out.open(newline="", encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 5


def _register_tree(conn, base, tmp_path):
    ids = {}
    for p in sorted(base.rglob("*")):
        if p.is_file() and not p.name.startswith("~$"):
            ids[p.name] = register_document(conn, p, actor="analyst", source_channel="TEST",
                                            archive_root=tmp_path / "arc").document_id
    return ids


def test_extract_all_stores_segments_and_registers_attachments(conn, tmp_path):
    base = _contracts_tree(tmp_path)
    ids = _register_tree(conn, base, tmp_path)
    summaries = extract_all_pending(conn, actor="analyst", work_root=tmp_path / "work",
                                    archive_root=tmp_path / "arc")
    by_doc = {s.document_id: s for s in summaries}
    msg = by_doc[ids["SYN_evergreen_email.msg"]]
    assert len(msg.attachments_registered) == 1
    att_name, att_id, outcome = msg.attachments_registered[0]
    assert outcome == "NEW" and att_id in by_doc            # attachment extracted in pass 2
    assert by_doc[att_id].status == "TEXT_EXTRACTED"
    att_doc = conn.execute(select(source_documents).where(
        source_documents.c.document_id == att_id)).mappings().one()
    assert att_doc["parent_document_id"] == ids["SYN_evergreen_email.msg"]
    assert att_doc["source_channel"] == "MSG_ATTACHMENT"
    assert att_doc["original_path"].endswith("SYN_evergreen_email.msg!/SYN_evergreen_agreement.pdf")
    assert by_doc[ids["SYN_scanned.pdf"]].status == "NEEDS_OCR"
    seg = conn.execute(select(document_segments.c.location, document_segments.c.text)
                       .join(document_extractions)
                       .where(document_extractions.c.document_id == ids["SYN_phys_confirm.pdf"])
                       ).all()
    assert [(s.location, s.text) for s in seg] == [("p1:L1", "SYNTHETIC physical confirm text")]
    assert audit.verify_chain(conn).ok


def test_extraction_is_idempotent(conn, tmp_path):
    base = _contracts_tree(tmp_path)
    _register_tree(conn, base, tmp_path)
    first = extract_all_pending(conn, actor="analyst", work_root=tmp_path / "w",
                                archive_root=tmp_path / "arc")
    assert extract_all_pending(conn, actor="analyst", work_root=tmp_path / "w",
                               archive_root=tmp_path / "arc") == []
    n = conn.execute(select(func.count()).select_from(document_extractions)).scalar()
    assert n == len(first) == 5                         # 4 files + 1 attachment
    again = extract_document(conn, first[0].document_id, actor="analyst",
                             work_root=tmp_path / "w", archive_root=tmp_path / "arc")
    assert again.already_done


def test_extraction_refuses_altered_evidence(conn, tmp_path):
    p = tmp_path / "SYN.pdf"
    p.write_bytes(build_pdf([["SYNTHETIC original text line"]]))
    reg = register_document(conn, p, actor="analyst", source_channel="TEST",
                            archive_root=tmp_path / "arc")
    os.chmod(reg.archive_uri, stat.S_IRUSR | stat.S_IWUSR)
    with open(reg.archive_uri, "wb") as fh:
        fh.write(build_pdf([["SYNTHETIC tampered text line"]]))
    with pytest.raises(EvidenceIntegrityError):
        extract_document(conn, reg.document_id, actor="analyst", work_root=tmp_path / "w",
                         archive_root=tmp_path / "arc")
    assert conn.execute(select(func.count()).select_from(document_extractions)).scalar() == 0


def test_hostile_attachment_names_stay_inside_work_root(conn, tmp_path):
    m = tmp_path / "SYN.msg"
    m.write_bytes(build_msg(attachments=[("../../escape.pdf", b"%PDF-x", 1),
                                         ("C:\\evil.pdf", b"%PDF-y", 1)]))
    reg = register_document(conn, m, actor="analyst", source_channel="TEST")
    s = extract_document(conn, reg.document_id, actor="analyst", work_root=tmp_path / "w",
                         archive_root=None)
    assert len(s.attachments_registered) == 2
    out_dir = tmp_path / "w" / "attachments" / reg.document_id
    written = [p for p in (tmp_path / "w").rglob("*") if p.is_file()]
    assert sorted(p.name for p in written) == ["escape.pdf", "evil.pdf"]
    assert all(p.parent == out_dir for p in written)      # '../' and 'C:\\' stripped
    assert not (tmp_path / "escape.pdf").exists() and not (tmp_path / "w" / "escape.pdf").exists()


def test_cli_profile_preview_and_extract(tmp_path, capsys):
    from riskparse.cli import main
    base = _contracts_tree(tmp_path)
    assert main(["profile-documents", "--root", str(base), "--out",
                 str(tmp_path / "p.csv")]) == 0
    assert "5 document(s) in total" in capsys.readouterr().out
    assert main(["extract-preview", str(base / "Financial Contracts" / "SYN_swap.xlsx")]) == 0
    out = capsys.readouterr().out
    assert "Confirm!B3" in out and "PARTIAL_TEXT" in out
    cfg = tmp_path / "s.toml"
    cfg.write_text(f'[environment]\nname="test"\n[database]\nbackend="sqlite"\n'
                   f'sqlite_path="{(tmp_path / "c.db").as_posix()}"\n[paths]\n'
                   f'archive_root="{(tmp_path / "arc").as_posix()}"\n'
                   f'work_root="{(tmp_path / "work").as_posix()}"\n')
    assert main(["--config", str(cfg), "db-upgrade"]) == 0
    assert main(["--config", str(cfg), "register-document",
                 str(base / "Evergreen Contracts" / "SYN_evergreen_email.msg"),
                 "--actor", "analyst"]) == 0
    assert main(["--config", str(cfg), "extract", "--all", "--actor", "analyst"]) == 0
    out = capsys.readouterr().out
    assert "attachment 'SYN_evergreen_agreement.pdf'" in out
    assert main(["--config", str(cfg), "audit-verify"]) == 0


# --- layout fingerprint ---------------------------------------------------------------

def _pdf_layout(lines):
    from riskparse.extract.dispatch import extract_bytes
    from riskparse.extract.profile import layout_key
    return layout_key(extract_bytes(build_pdf([lines]), "PDF"), "PDF")


def test_layout_key_groups_same_template_with_different_values():
    a = _pdf_layout(["SYNTHETIC CPTY ALPHA CONFIRMATION", "Buyer: Alpha", "Seller: Us",
                     "Price: 2.50 CAD/GJ", "Volume: 1000 GJ/d", "Delivery Point: AECO"])
    b = _pdf_layout(["SYNTHETIC CPTY ALPHA CONFIRMATION", "Buyer: Alpha", "Seller: Us",
                     "Price: 3.10 CAD/GJ", "Volume: 250 GJ/d", "Delivery Point: Station 2"])
    c = _pdf_layout(["SYNTHETIC BRAVO", "Counterparty: Bravo", "Fixed Price: 2.5",
                     "Quantity: 10", "Hub: AECO"])
    assert a == b and a.startswith("P-") and a != c


def test_layout_key_empty_when_too_few_labels():
    assert _pdf_layout(["SYNTHETIC free text with no labels at all"]) == ""


def test_layout_key_reveals_no_content(tmp_path):
    base = _contracts_tree(tmp_path)
    keys = [r["layout_key"] for r in profile_folder(base).rows if r["layout_key"]]
    assert keys and all(len(k) == 12 for k in keys)                # e.g. "P-1a2b3c4d5e"


def test_msg_layout_key_uses_sender_domain():
    from riskparse.extract.dispatch import extract_bytes
    from riskparse.extract.profile import layout_key
    k1 = layout_key(extract_bytes(build_msg(sender_smtp="a@cpty-one.invalid"), "MSG"), "MSG")
    k2 = layout_key(extract_bytes(build_msg(sender_smtp="b@CPTY-ONE.invalid", body="x"), "MSG"),
                    "MSG")
    k3 = layout_key(extract_bytes(build_msg(sender_smtp="a@cpty-two.invalid"), "MSG"), "MSG")
    assert k1 == k2 != k3
