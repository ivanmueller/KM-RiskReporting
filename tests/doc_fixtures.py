"""Synthetic document builders for tests: .msg (OLE compound file), text PDF, .xlsx.

Nothing here is a real contract. Every fixture contains the word SYNTHETIC.

The compound-file writer implements just enough of MS-CFB (version 3, 512-byte sectors,
mini stream for streams under 4096 bytes, balanced directory tree) for olefile to read
it, and follows the MS-OXMSG stream naming used by Outlook.
"""

from __future__ import annotations

import io
import math
import struct
from datetime import UTC, datetime

SECTOR, MINI, CUTOFF = 512, 64, 4096
ENDOFCHAIN, FREESECT, FATSECT, NOSTREAM = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFF


class _Node:
    def __init__(self, name, kind, data=b""):
        self.name, self.kind, self.data = name, kind, data
        self.children: dict[str, _Node] = {}
        self.id = 0
        self.left = self.right = self.child = NOSTREAM
        self.start, self.size = ENDOFCHAIN, 0


def _key(n: _Node):
    return (len(n.name), n.name.upper())


def _tree(nodes: list[_Node]) -> int:
    if not nodes:
        return NOSTREAM
    mid = len(nodes) // 2
    n = nodes[mid]
    n.left, n.right = _tree(nodes[:mid]), _tree(nodes[mid + 1:])
    return n.id


def build_cfb(streams: dict[str, bytes]) -> bytes:
    root = _Node("Root Entry", 5)
    for path, data in streams.items():
        cur = root
        parts = path.split("/")
        for p in parts[:-1]:
            cur = cur.children.setdefault(p, _Node(p, 1))
        cur.children[parts[-1]] = _Node(parts[-1], 2, data)
    order, queue = [], [root]
    while queue:
        n = queue.pop(0)
        n.id = len(order)
        order.append(n)
        queue.extend(sorted(n.children.values(), key=_key))
    for n in order:
        n.child = _tree(sorted(n.children.values(), key=_key))

    mini, minifat, big = bytearray(), [], []
    for n in order:
        if n.kind != 2:
            continue
        n.size = len(n.data)
        if n.size == 0:
            continue
        if n.size < CUTOFF:
            count = math.ceil(n.size / MINI)
            n.start = len(minifat)
            minifat += [n.start + i + 1 for i in range(count - 1)] + [ENDOFCHAIN]
            mini += n.data.ljust(count * MINI, b"\0")
        else:
            big.append(n)

    fat: list[int] = []
    body = bytearray()

    def alloc(data: bytes) -> int:
        if not data:
            return ENDOFCHAIN
        count = math.ceil(len(data) / SECTOR)
        start = len(fat)
        fat.extend([start + i + 1 for i in range(count - 1)] + [ENDOFCHAIN])
        body.extend(data.ljust(count * SECTOR, b"\0"))
        return start

    entries = len(order)
    dir_sectors = math.ceil(entries * 128 / SECTOR)
    dir_start = len(fat)
    fat.extend([dir_start + i + 1 for i in range(dir_sectors - 1)] + [ENDOFCHAIN])
    body.extend(b"\0" * dir_sectors * SECTOR)            # filled in below
    root.start = alloc(bytes(mini))
    root.size = len(mini)
    minifat_bytes = b"".join(struct.pack("<I", x) for x in minifat)
    minifat_bytes += struct.pack("<I", FREESECT) * ((-len(minifat)) % 128)
    minifat_start = alloc(minifat_bytes) if minifat else ENDOFCHAIN
    minifat_sectors = math.ceil(len(minifat_bytes) / SECTOR) if minifat else 0
    for n in big:
        n.start = alloc(n.data)

    n_fat = 1
    while math.ceil((len(fat) + n_fat) / 128) > n_fat:
        n_fat += 1
    fat_start = len(fat)
    fat.extend([FATSECT] * n_fat)
    fat += [FREESECT] * (n_fat * 128 - len(fat))
    body.extend(b"".join(struct.pack("<I", x) for x in fat))

    d = bytearray()
    for n in order:
        name = n.name.encode("utf-16-le") + b"\0\0"
        d += name.ljust(64, b"\0") + struct.pack("<HBB", len(name), n.kind, 1)
        d += struct.pack("<III", n.left, n.right, n.child) + b"\0" * 16 + b"\0" * 4 + b"\0" * 16
        d += struct.pack("<IQ", n.start, n.size if n.kind != 1 else 0)
    while len(d) % SECTOR:   # unused entries: 128 bytes each, no siblings/child
        d += (b"\0" * 64 + struct.pack("<HBB", 0, 0, 0)
              + struct.pack("<III", NOSTREAM, NOSTREAM, NOSTREAM) + b"\0" * 48)
    off = dir_start * SECTOR
    body[off: off + len(d)] = d

    difat = [fat_start + i for i in range(n_fat)] + [FREESECT] * (109 - n_fat)
    header = (b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\0" * 16
              + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
              + struct.pack("<IIIIIIIII", 0, n_fat, dir_start, 0, CUTOFF, minifat_start,
                            minifat_sectors, ENDOFCHAIN, 0)
              + b"".join(struct.pack("<I", x) for x in difat))
    assert len(header) == 512
    assert len(d) % SECTOR == 0 and len(d) == dir_sectors * SECTOR
    return header + bytes(body)


def _filetime(dt: datetime) -> int:
    return int((dt - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 10_000_000)


def _prop_entry(prop: int, ptype: int, value: bytes) -> bytes:
    return struct.pack("<II", (prop << 16) | ptype, 6) + value.ljust(8, b"\0")


def build_msg(*, subject="SYNTHETIC confirmation", body: str | None = "SYNTHETIC body",
              sender="SYNTHETIC Sender", sender_smtp="sender@synthetic.invalid",
              to="risk@synthetic.invalid", sent: datetime | None = None,
              attachments=(), html: str | None = None, unicode=True, codepage: int | None = None,
              rtf_only=False) -> bytes:
    """``attachments``: iterable of (filename, bytes or None, method_code)."""
    st: dict[str, bytes] = {}

    def put(prefix, prop, text):
        if unicode:
            st[f"{prefix}__substg1.0_{prop:04X}001F"] = text.encode("utf-16-le")
        else:
            st[f"{prefix}__substg1.0_{prop:04X}001E"] = text.encode(f"cp{codepage or 1252}")

    put("", 0x0037, subject)
    put("", 0x0C1A, sender)
    put("", 0x5D01, sender_smtp)
    put("", 0x0E04, to)
    if body is not None:
        put("", 0x1000, body)
    if html is not None:
        st["__substg1.0_10130102"] = html.encode(f"cp{codepage or 1252}")
    if rtf_only:
        st["__substg1.0_10090102"] = b"LZFu-synthetic-compressed-rtf"
    props = b"\0" * 32
    props += _prop_entry(0x0039, 0x0040,
                         struct.pack("<Q", _filetime(sent or datetime(2026, 10, 5, 15, 30,
                                                                       tzinfo=UTC))))
    if codepage:
        props += _prop_entry(0x3FFD, 0x0003, struct.pack("<i", codepage))
    st["__properties_version1.0"] = props
    for i, (name, data, method) in enumerate(attachments):
        pre = f"__attach_version1.0_#{i:08X}/"
        put(pre, 0x3707, name)
        if data is not None and method == 5:
            st[f"{pre}__substg1.0_3701000D/__substg1.0_0037001F"] = "inner".encode("utf-16-le")
        elif data is not None:
            st[f"{pre}__substg1.0_37010102"] = data
        st[f"{pre}__properties_version1.0"] = b"\0" * 8 + _prop_entry(0x3705, 0x0003,
                                                                      struct.pack("<i", method))
    return build_cfb(st)


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: list[list[str]]) -> bytes:
    """Text PDF; an empty list for a page produces a page with no text layer."""
    objs: list[bytes] = []
    n_pages = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        content_id = 5 + 2 * i
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
                    f"{content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode())
        ops = ["BT", "/F1 11 Tf", "72 720 Td", "14 TL"]
        for line in lines:
            ops.append(f"({_pdf_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1") if lines else b""
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
                    + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offsets:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
              .encode())
    return out.getvalue()


def build_xlsx() -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Confirm"
    ws["A1"] = "SYNTHETIC CONFIRMATION"
    ws["A2"], ws["B2"] = "Price", 2.65
    ws["A3"], ws["B3"] = "Discount", 0.05
    ws["B3"].number_format = "0%"
    ws["A4"], ws["B4"] = "Trade Date", datetime(2026, 10, 5)
    ws["A5"], ws["B5"] = "Double", "=B2*2"            # never calculated: no cached value
    ws["A6"] = "   "                                  # whitespace only: skipped
    hidden = wb.create_sheet("Workings")
    hidden["A1"] = "SYNTHETIC hidden note"
    hidden.sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
