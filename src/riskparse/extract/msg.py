"""Outlook .msg extraction (MS-OXMSG) using olefile (BSD), not GPL-licensed readers.

A .msg file is an OLE compound file. Each MAPI property is a stream named
``__substg1.0_<TAG><TYPE>``; fixed-size properties sit in ``__properties_version1.0``.
This module reads only what is needed for evidence:

* headers: subject, sender name/address, To, Cc, sent/received time, transport headers;
* the plain-text body, or the HTML body rendered to text if no plain body exists
  (RTF-only bodies are reported, not decoded);
* attachments, returned as bytes so they can be registered as child documents of the
  email. Embedded messages and by-reference attachments are reported, not extracted.

The email body is kept even when an attachment exists: the game plan requires
identifying which text is legally operative, so neither part is dropped.
"""

from __future__ import annotations

import io
import struct
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import olefile

from riskparse.extract.base import (
    FAILED,
    PARTIAL_TEXT,
    TEXT_EXTRACTED,
    Attachment,
    ExtractionResult,
    SegmentBuilder,
)

NAME = "msg-oxmsg"
VERSION = "1"

PT_STRING8, PT_UNICODE, PT_BINARY, PT_LONG, PT_SYSTIME, PT_OBJECT = (
    0x001E, 0x001F, 0x0102, 0x0003, 0x0040, 0x000D)

PR_SUBJECT = 0x0037
PR_CLIENT_SUBMIT_TIME = 0x0039
PR_TRANSPORT_HEADERS = 0x007D
PR_SENDER_NAME = 0x0C1A
PR_SENDER_EMAIL = 0x0C1F
PR_SENDER_SMTP = 0x5D01
PR_DISPLAY_CC = 0x0E03
PR_DISPLAY_TO = 0x0E04
PR_MESSAGE_DELIVERY_TIME = 0x0E06
PR_BODY = 0x1000
PR_RTF_COMPRESSED = 0x1009
PR_HTML = 0x1013
PR_MESSAGE_CODEPAGE = 0x3FFD
PR_ATTACH_DATA = 0x3701
PR_ATTACH_FILENAME = 0x3704
PR_ATTACH_METHOD = 0x3705
PR_ATTACH_LONG_FILENAME = 0x3707
PR_DISPLAY_NAME = 0x3001

ATTACH_METHODS = {1: "BY_VALUE", 2: "BY_REFERENCE", 4: "BY_REFERENCE", 5: "EMBEDDED_MESSAGE",
                  6: "OLE", 7: "BY_REFERENCE"}
MAX_ATTACHMENTS = 500
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


class _Html2Text(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


class _Props:
    """Reads MAPI properties for one storage (message root or an attachment)."""

    def __init__(self, ole: olefile.OleFileIO, prefix: list[str], header_size: int,
                 codepage: str = "cp1252"):
        self.ole, self.prefix, self.codepage = ole, prefix, codepage
        self.fixed: dict[int, tuple[int, bytes]] = {}
        path = prefix + ["__properties_version1.0"]
        if ole.exists("/".join(path)):
            raw = ole.openstream(path).read()
            for off in range(header_size, len(raw) - 15, 16):
                tag, _flags = struct.unpack_from("<II", raw, off)
                self.fixed[tag >> 16] = (tag & 0xFFFF, raw[off + 8: off + 16])

    def _stream(self, prop: int, ptype: int) -> bytes | None:
        path = self.prefix + [f"__substg1.0_{prop:04X}{ptype:04X}"]
        if self.ole.exists("/".join(path)):
            return self.ole.openstream(path).read()
        return None

    def string(self, prop: int) -> str | None:
        raw = self._stream(prop, PT_UNICODE)
        if raw is not None:
            return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        raw = self._stream(prop, PT_STRING8)
        if raw is not None:
            return raw.decode(self.codepage, errors="replace").rstrip("\x00")
        return None

    def binary(self, prop: int) -> bytes | None:
        return self._stream(prop, PT_BINARY)

    def long(self, prop: int) -> int | None:
        entry = self.fixed.get(prop)
        if entry and entry[0] == PT_LONG:
            return struct.unpack_from("<i", entry[1])[0]
        return None

    def time(self, prop: int) -> datetime | None:
        entry = self.fixed.get(prop)
        if entry and entry[0] == PT_SYSTIME:
            ticks = struct.unpack_from("<Q", entry[1])[0]
            if ticks:
                return _FILETIME_EPOCH + timedelta(microseconds=ticks // 10)
        return None

    def has_object(self, prop: int) -> bool:
        return self.ole.exists("/".join(self.prefix + [f"__substg1.0_{prop:04X}{PT_OBJECT:04X}"]))


def _codepage_name(cp: int | None) -> str:
    if cp in (None, 0):
        return "cp1252"
    if cp == 65001:
        return "utf-8"
    name = f"cp{cp}"
    try:
        "".encode(name)
        return name
    except LookupError:
        return "cp1252"


def extract_msg(data: bytes, *, filename: str = "") -> ExtractionResult:
    result = ExtractionResult(NAME, VERSION, TEXT_EXTRACTED)
    if not olefile.isOleFile(io.BytesIO(data)):
        result.status = FAILED
        result.warnings.append("not an OLE compound file (.msg); it may be a renamed .eml or "
                               "corrupt")
        return result
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:
        result.status = FAILED
        result.warnings.append(f"could not open .msg: {type(exc).__name__}: {exc}")
        return result
    try:
        root = _Props(ole, [], header_size=32)
        root.codepage = _codepage_name(root.long(PR_MESSAGE_CODEPAGE))
        seg = SegmentBuilder()
        headers = {
            "Subject": root.string(PR_SUBJECT),
            "From-Name": root.string(PR_SENDER_NAME),
            "From-Address": root.string(PR_SENDER_SMTP) or root.string(PR_SENDER_EMAIL),
            "To": root.string(PR_DISPLAY_TO),
            "Cc": root.string(PR_DISPLAY_CC),
        }
        for key, value in headers.items():
            if value:
                seg.add(f"header:{key}", "header", value)
        times = {}
        for key, prop in (("Sent", PR_CLIENT_SUBMIT_TIME), ("Received", PR_MESSAGE_DELIVERY_TIME)):
            t = root.time(prop)
            if t is not None:
                times[key] = t.isoformat()
                seg.add(f"header:{key}", "header", t.isoformat())
        transport = root.string(PR_TRANSPORT_HEADERS)
        if transport:
            seg.add_lines("transport:", "transport_header", transport)

        body = root.string(PR_BODY)
        body_source = None
        if body and body.strip():
            body_source = "plain"
            seg.add_lines("body:", "body_line", body)
        else:
            html_raw = root.binary(PR_HTML)
            html = html_raw.decode(root.codepage, errors="replace") if html_raw else \
                root.string(PR_HTML)
            if html:
                body_source = "html"
                parser = _Html2Text()
                parser.feed(html)
                seg.add_lines("html_body:", "html_body_line", parser.text())
            elif root.binary(PR_RTF_COMPRESSED):
                body_source = "rtf_only"
                result.status = PARTIAL_TEXT
                result.warnings.append("body is stored only as compressed RTF, which is not "
                                       "decoded; read the body in Outlook and record it manually")

        attachments: list[Attachment] = []
        storages = sorted({e[0] for e in ole.listdir(streams=True, storages=True)
                           if e and e[0].startswith("__attach_version1.0_#")})
        if len(storages) > MAX_ATTACHMENTS:
            raise ValueError(f"{len(storages)} attachments exceeds limit {MAX_ATTACHMENTS}")
        for index, storage in enumerate(storages, start=1):
            props = _Props(ole, [storage], header_size=8, codepage=root.codepage)
            name = (props.string(PR_ATTACH_LONG_FILENAME) or props.string(PR_ATTACH_FILENAME)
                    or props.string(PR_DISPLAY_NAME) or f"attachment_{index}")
            method_code = props.long(PR_ATTACH_METHOD)
            method = ATTACH_METHODS.get(method_code or 1, "UNKNOWN")
            payload = props.binary(PR_ATTACH_DATA)
            if props.has_object(PR_ATTACH_DATA) and method == "BY_VALUE":
                method = "EMBEDDED_MESSAGE" if method_code == 5 else "OLE"
            note = ""
            if method == "EMBEDDED_MESSAGE":
                payload, note = None, ("embedded email not extracted: open the parent in Outlook, "
                                       "save the inner email as .msg and register it separately")
            elif method == "BY_REFERENCE":
                payload, note = None, "attachment is a link to an external file; obtain the file"
            elif payload is None:
                note = "no attachment data found"
            attachments.append(Attachment(index, name, payload, method, note))
            seg.add(f"attachment:{index}", "attachment", name, method=method,
                    size=len(payload) if payload is not None else None)
            if note:
                result.warnings.append(f"attachment {index} '{name}': {note}")
        result.segments = seg.segments
        result.attachments = attachments
        result.metadata = {"codepage": root.codepage, "body_source": body_source,
                           "attachments": len(attachments),
                           "attachment_names": [a.filename for a in attachments], **times}
        if any(a.data is None for a in attachments) and result.status == TEXT_EXTRACTED:
            result.status = PARTIAL_TEXT
        if body_source is None and not attachments:
            result.status = PARTIAL_TEXT
            result.warnings.append("email has no body and no attachments")
    except Exception as exc:
        result.status = FAILED
        result.warnings.append(f"could not parse .msg: {type(exc).__name__}: {exc}")
        result.segments, result.attachments = [], []
    finally:
        ole.close()
    return result
