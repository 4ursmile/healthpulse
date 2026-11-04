"""Small shared helpers: ids, clocks, hashing, Brazil epidemiological weeks, JSON."""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import orjson

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """Time-sortable 26-char id (ULID layout: 48-bit ms timestamp + 80 random bits)."""
    ms = int(time.time() * 1000)
    rnd = int.from_bytes(secrets.token_bytes(10), "big")
    n = (ms << 80) | rnd
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    return f"{prefix}_{ulid().lower()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s: str | datetime | None) -> datetime | None:
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def stamp(dt: datetime | None = None) -> str:
    return (dt or utcnow()).strftime("%Y%m%dT%H%M%SZ")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def stable_hash(obj: Any) -> str:
    """Content hash of a JSON-able object with sorted keys — used for configs, manifests and feature expressions."""
    return hashlib.sha256(orjson.dumps(obj, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS, default=str)).hexdigest()


def dumps(obj: Any) -> str:
    return orjson.dumps(obj, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY, default=str).decode()


def loads(s: str | bytes | None) -> Any:
    if s is None or s == "":
        return None
    return orjson.loads(s)


def write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_bytes(orjson.dumps(obj, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY, default=str))
    os.replace(tmp, path)


# ---------------------------------------------------------------- epidemiological weeks
# Brazil (SINAN) uses Sunday-start weeks; week 1 is the week that contains at least four days of the new year
# (the same rule as CDC MMWR weeks).

def epi_week_start(d: date) -> date:
    """Sunday on or before d."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _epi_year_start(year: int) -> date:
    jan1 = date(year, 1, 1)
    start = epi_week_start(jan1)
    if (jan1 - start).days >= 4:  # Jan 1 falls Thu..Sat → week 1 starts the following Sunday
        start += timedelta(days=7)
    return start


def epi_week(d: date) -> tuple[int, int]:
    """(epi_year, epi_week) for a date."""
    ws = epi_week_start(d)
    y = ws.year + 1
    while _epi_year_start(y) > ws:
        y -= 1
    return y, (ws - _epi_year_start(y)).days // 7 + 1


def epi_week_code(d: date) -> int:
    y, w = epi_week(d)
    return y * 100 + w


def week_from_code(code: int) -> date:
    y, w = divmod(int(code), 100)
    return _epi_year_start(y) + timedelta(days=7 * (w - 1))


def last_complete_week(now: datetime | None = None) -> date:
    """Start of the last epi week that has fully ended (default for every product screen)."""
    d = (now or utcnow()).date()
    return epi_week_start(d) - timedelta(days=7)
