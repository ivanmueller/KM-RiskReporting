"""Normalization of person identities used in separation-of-duties checks.

Two spellings of the same person (``Pat``, ``pat ``, ``ＰＡＴ`` in full-width
characters) must not count as two different people. Identities are normalized with
Unicode NFKC, surrounding whitespace removed, internal whitespace collapsed and
lower-cased before being stored or compared. The database also compares with
``LOWER()`` (migration 0002) as a backstop.

This does not prove identity: it only prevents trivial spelling variants. Once IT
confirms the directory/authentication model, actors should be directory login IDs
taken from the authenticated session, not typed names (game plan 9.6).
"""

from __future__ import annotations

import re
import unicodedata

MAX_LENGTH = 100
_WS = re.compile(r"\s+")


class InvalidIdentity(ValueError):
    pass


def normalize_actor(name: str | None) -> str:
    if name is None:
        raise InvalidIdentity("identity is required")
    text = unicodedata.normalize("NFKC", str(name))
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text):
        raise InvalidIdentity("identity contains control or invisible formatting characters")
    text = _WS.sub(" ", text).strip().lower()
    if not text:
        raise InvalidIdentity("identity is blank")
    if len(text) > MAX_LENGTH:
        raise InvalidIdentity(f"identity longer than {MAX_LENGTH} characters")
    return text


def same_person(a: str | None, b: str | None) -> bool:
    """True when both are given and normalize to the same identity."""
    if not a or not b:
        return False
    try:
        return normalize_actor(a) == normalize_actor(b)
    except InvalidIdentity:
        return False
