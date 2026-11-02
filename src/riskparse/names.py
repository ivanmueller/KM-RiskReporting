"""Counterparty master names (``config/counterparty_map.csv``).

Brokers spell the same company differently ("SECURE Waste Infrastructure Corp." vs
"Secure Waste Infrastructure Corp.", "... Trading & Supply ULC" vs "... Trading and Supply
ULC"). The map turns every spelling into one master name, for the Counterparty and Seller
columns.

The file is a plain CSV that Risk maintains, in Excel or any editor:

    name_as_written,master_name,notes

A name is matched exactly first. Failing that, it is matched ignoring only case, full stops,
commas, apostrophes, spacing and "&" versus "and". A name that is not in the map is kept as
written and flagged, never guessed. The review lists every spelling on its ``names`` sheet,
so new ones can be added to the map.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAP = Path("config/counterparty_map.csv")
COLUMNS = ("name_as_written", "master_name", "notes")
PARTY_FIELDS = ("counterparty", "seller", "buyer")


class NameMapError(ValueError):
    pass


def norm(name: str) -> str:
    n = name.casefold().replace("&", " and ")
    n = re.sub(r"[.,']", " ", n)
    return " ".join(n.split())


@dataclass
class NameMap:
    path: Path | None = None
    exact: dict[str, str] = field(default_factory=dict)
    loose: dict[str, str] = field(default_factory=dict)

    def lookup(self, name: str) -> tuple[str | None, str]:
        n = " ".join((name or "").split())
        if not n:
            return None, ""
        if n in self.exact:
            return self.exact[n], "exact"
        hit = self.loose.get(norm(n))
        return (hit, "ignoring case/punctuation") if hit else (None, "")


def load_name_map(path: str | Path) -> NameMap:
    p = Path(path)
    if not p.is_file():
        raise NameMapError(f"counterparty map not found: {p}")
    try:
        text = p.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NameMapError(f"{p} is not UTF-8 (save it as 'CSV UTF-8' in Excel)") from exc
    reader = csv.DictReader(text.splitlines())
    missing = [c for c in COLUMNS[:2] if c not in (reader.fieldnames or [])]
    if missing:
        raise NameMapError(f"{p} needs the columns {list(COLUMNS)}; missing {missing}")
    nmap = NameMap(p)
    for line, row in enumerate(reader, start=2):
        written = " ".join((row.get("name_as_written") or "").split())
        master = " ".join((row.get("master_name") or "").split())
        if not written and not master:
            continue
        if not written or not master:
            raise NameMapError(f"{p} line {line}: both name_as_written and master_name are "
                               "required")
        for table, key in ((nmap.exact, written), (nmap.loose, norm(written))):
            if table.get(key, master) != master:
                raise NameMapError(f"{p} line {line}: '{written}' maps to both "
                                   f"'{table[key]}' and '{master}'")
            table[key] = master
    return nmap


def apply_name_map(trades, nmap: NameMap | None) -> None:
    """Replace party names with master names; record the original; flag unmapped ones."""
    for t in trades:
        for attr in PARTY_FIELDS:
            raw = getattr(t, attr)
            if not raw:
                continue
            t.names_as_written[attr] = raw
            if nmap is None:
                continue
            master, how = nmap.lookup(raw)
            if master is None:
                if attr == "counterparty":
                    t.flags.append(f"counterparty '{raw}' is not in the counterparty map; "
                                   "kept as written")
                continue
            setattr(t, attr, master)
            if master != raw and attr in t.evidence:
                t.evidence[attr] += f"  -> master name '{master}' ({how})"
