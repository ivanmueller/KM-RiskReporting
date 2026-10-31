"""Text-layer PDF extraction with page and line locations (pypdf, BSD-3-Clause).

Only the existing text layer is read: no JavaScript, forms, links or embedded files are
processed, and no OCR is attempted. A page with (almost) no text is reported, and a
document with no text on any page is ``NEEDS_OCR``: a scanned confirmation must go to a
reviewed OCR route with human verification, not be guessed at.
"""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader
from pypdf.errors import DependencyError, FileNotDecryptedError, PdfReadError

from riskparse.extract.base import (
    ENCRYPTED,
    FAILED,
    NEEDS_OCR,
    PARTIAL_TEXT,
    TEXT_EXTRACTED,
    ExtractionResult,
    SegmentBuilder,
)

NAME = "pdf-text"
VERSION = "1"
MIN_PAGE_CHARS = 20          # fewer non-space characters than this = "no text layer"
MAX_PAGES = 5_000


def extract_pdf(data: bytes, *, filename: str = "") -> ExtractionResult:
    result = ExtractionResult(NAME, VERSION, TEXT_EXTRACTED)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")
            except (DependencyError, NotImplementedError) as exc:
                result.status = ENCRYPTED
                result.warnings.append(f"encrypted PDF; cannot decrypt here: {exc}")
                return result
            if not opened:
                result.status = ENCRYPTED
                result.warnings.append("password-protected PDF; obtain an unprotected copy")
                return result
            result.warnings.append("PDF is encrypted with an empty user password; text read")
        pages = reader.pages
        if len(pages) > MAX_PAGES:
            result.status = FAILED
            result.warnings.append(f"{len(pages)} pages exceeds limit {MAX_PAGES}")
            return result
        seg = SegmentBuilder()
        empty_pages: list[int] = []
        for number, page in enumerate(pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                result.warnings.append(f"page {number}: text extraction failed: {exc}")
                text = ""
            if len("".join(text.split())) < MIN_PAGE_CHARS:
                empty_pages.append(number)
            seg.add_lines(f"p{number}:", "line", text)
        result.segments = seg.segments
        result.metadata = {"pages": len(pages), "pages_without_text": empty_pages}
    except FileNotDecryptedError as exc:
        result.status = ENCRYPTED
        result.warnings.append(str(exc))
        return result
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        result.status = FAILED
        result.warnings.append(f"could not read PDF: {type(exc).__name__}: {exc}")
        return result

    if empty_pages and len(empty_pages) == result.metadata["pages"]:
        result.status = NEEDS_OCR
        result.warnings.append("no text layer on any page (scanned?): needs a reviewed OCR "
                               "route and human verification")
    elif empty_pages:
        result.status = PARTIAL_TEXT
        result.warnings.append(f"page(s) {empty_pages} have no text layer (scanned or "
                               "image-only); their content is not extracted")
    return result
