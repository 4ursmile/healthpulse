"""Minimal, dependency-free .xlsx reader (first worksheet or by index) for small IBGE tables.
Only values are read (shared strings, inline strings, numbers); no formulas are evaluated and no macros exist in xlsx."""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_COL = re.compile(r"([A-Z]+)(\d+)")


def _col_index(ref: str) -> tuple[int, int]:
    m = _COL.match(ref)
    letters, row = m.group(1), int(m.group(2))
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return row - 1, n - 1


def read_rows(data: bytes, sheet: int = 1, max_bytes: int = 200_000_000) -> list[list]:
    z = zipfile.ZipFile(io.BytesIO(data))
    if sum(i.file_size for i in z.infolist()) > max_bytes:
        raise ValueError("xlsx expands beyond the allowed size (zip bomb guard)")
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    rows: dict[int, dict[int, object]] = {}
    for c in root.iter(f"{{{_NS['m']}}}c"):
        r, k = _col_index(c.get("r"))
        t = c.get("t")
        v = c.find("m:v", _NS)
        if t == "s" and v is not None:
            val: object = shared[int(v.text)]
        elif t == "inlineStr":
            val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
        elif v is not None and v.text is not None:
            try:
                f = float(v.text)
                val = int(f) if f.is_integer() else f
            except ValueError:
                val = v.text
        else:
            continue
        rows.setdefault(r, {})[k] = val
    if not rows:
        return []
    width = max(max(r) for r in rows.values()) + 1
    return [[rows.get(i, {}).get(j) for j in range(width)] for i in range(max(rows) + 1)]
