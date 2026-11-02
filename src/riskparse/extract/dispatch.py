"""Pick the extractor for a file type and run it safely."""

from __future__ import annotations

from collections.abc import Callable

from riskparse.extract import msg, pdf, xls, xlsx
from riskparse.extract.base import (
    FAILED,
    UNSUPPORTED,
    ExtractionLimitExceeded,
    ExtractionResult,
)

# file type -> (function, extractor name, extractor version)
EXTRACTORS: dict[str, tuple[Callable[..., ExtractionResult], str, str]] = {
    "PDF": (pdf.extract_pdf, pdf.NAME, pdf.VERSION),
    "XLSX": (xlsx.extract_xlsx, xlsx.NAME, xlsx.VERSION),
    "XLSM": (xlsx.extract_xlsx, xlsx.NAME, xlsx.VERSION),
    "XLS": (xls.extract_xls, xls.NAME, xls.VERSION),
    "MSG": (msg.extract_msg, msg.NAME, msg.VERSION),
}


def extractor_for(file_type: str) -> tuple[str, str] | None:
    entry = EXTRACTORS.get(file_type)
    return (entry[1], entry[2]) if entry else None


def extract_bytes(data: bytes, file_type: str, *, filename: str = "") -> ExtractionResult:
    entry = EXTRACTORS.get(file_type)
    if entry is None:
        r = ExtractionResult("none", "0", UNSUPPORTED)
        r.warnings.append(f"no extractor for file type {file_type}")
        return r
    fn, name, version = entry
    try:
        return fn(data, filename=filename)
    except ExtractionLimitExceeded as exc:
        r = ExtractionResult(name, version, FAILED)
        r.warnings.append(f"extraction stopped: {exc}")
        return r
