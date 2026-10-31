"""Layout-neutral text extraction with evidence locations (Stage 1, Week 3 foundation).

This layer turns a document's bytes into **segments**: pieces of raw text, each with an
exact location (``Sheet!B7``, ``p3:L12``, ``header:Subject``, ``body:L4``). It does not
interpret anything as a price, volume, side or date; that is the job of later, reviewed,
counterparty-specific templates, which will cite these locations as evidence.

Safety rules:

* Files are never opened by Office/Outlook/Acrobat, and no macro, script, link or form is
  executed. Excel formulas are not evaluated; only the value Excel last saved is read.
* Extractors work on bytes already verified against the registered SHA-256.
* A file that cannot be read fully is reported (``PARTIAL_TEXT``, ``NEEDS_OCR``,
  ``ENCRYPTED``, ``UNSUPPORTED``, ``FAILED``), never silently skipped.
* Text extracted from documents is data. It is never treated as instructions.
"""
