"""Bronze / Silver / Gold layout, atomic publication and a safe allowlisted HTTP client."""
from __future__ import annotations

import os
import random
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import polars as pl

from ..config import get_settings
from .util import sha256_bytes, sha256_file, stamp, utcnow, write_json_atomic


class UnsafeURL(ValueError):
    pass


class FetchError(RuntimeError):
    pass


def check_url(url: str, approved: tuple[str, ...] | list[str] | None = None) -> None:
    """SSRF control: https only, allowlisted hostnames, no credentials in the URL, no IP literals."""
    u = urlparse(url)
    hosts = tuple(approved) if approved else get_settings().approved_hosts
    if u.scheme != "https":
        raise UnsafeURL(f"only https is allowed (got {u.scheme or 'none'})")
    if u.username or u.password:
        raise UnsafeURL("credentials in URLs are not allowed; use a secret reference")
    host = (u.hostname or "").lower()
    if not host or host.replace(".", "").isdigit() or ":" in host:
        raise UnsafeURL("IP literals and empty hosts are not allowed")
    if host not in hosts:
        raise UnsafeURL(f"host {host} is not on the approved list")


def fetch(url: str, *, params: dict | None = None, approved: tuple[str, ...] | list[str] | None = None,
          max_bytes: int | None = None, expect: str | None = None, retries: int = 4, timeout: float | None = None,
          min_interval_s: float = 0.0, user_agent: str | None = None) -> tuple[bytes, dict]:
    """GET with bounded size, content sniffing, redirects re-checked against the allowlist, and jittered backoff."""
    s = get_settings()
    if s.offline:
        raise FetchError("offline mode: network fetch disabled (HP_OFFLINE=1)")
    check_url(url, approved)
    limit = max_bytes or s.max_download_bytes
    last: Exception | None = None
    for attempt in range(retries):
        if min_interval_s:
            time.sleep(min_interval_s)
        try:
            with httpx.Client(timeout=timeout or s.http_timeout_s, follow_redirects=False,
                              headers={"User-Agent": user_agent or "HealthPulse/3.1 (+public-health research pilot)"}) as cli:
                target, hops = url, 0
                while True:
                    with cli.stream("GET", target, params=params if hops == 0 else None) as r:
                        if r.status_code in (301, 302, 303, 307, 308):
                            target = str(httpx.URL(target).join(r.headers["location"]))
                            check_url(target, approved)
                            hops += 1
                            if hops > 5:
                                raise FetchError("too many redirects")
                            continue
                        if r.status_code == 429 or r.status_code >= 500:
                            raise FetchError(f"HTTP {r.status_code}")
                        if r.status_code >= 400:
                            raise FetchError(f"HTTP {r.status_code} (not retried)")
                        if r.status_code in (202, 204):  # bot-challenge / empty "accepted" pages are not data
                            raise FetchError(f"HTTP {r.status_code} with no data (not retried)")
                        buf = bytearray()
                        for chunk in r.iter_bytes():
                            buf.extend(chunk)
                            if len(buf) > limit:
                                raise FetchError(f"response exceeds {limit} bytes")
                        meta = {"status": r.status_code, "content_type": r.headers.get("content-type", ""),
                                "final_url": target, "bytes": len(buf)}
                        body = bytes(buf)
                        _sniff(body, meta["content_type"], expect)
                        return body, meta
        except FetchError as e:
            last = e
            if "not retried" in str(e):
                break
        except (httpx.HTTPError, OSError) as e:
            last = e
        time.sleep(min(30.0, (2 ** attempt) + random.random()))
    raise FetchError(f"fetch failed for {url}: {last}")


def fetch_to_file(url: str, dest: Path, *, approved: tuple[str, ...] | list[str] | None = None, max_bytes: int | None = None,
                  expect: str | None = None, retries: int = 4, timeout: float = 600.0, progress=None,
                  user_agent: str | None = None) -> dict:
    """Streaming GET straight to disk (large bulk files: SRAG parquet, Zenodo archives, rasters). Same SSRF allowlist,
    redirect re-checks, size limit and HTML sniffing as `fetch`; the file is only moved into place when complete."""
    s = get_settings()
    if s.offline:
        raise FetchError("offline mode: network fetch disabled (HP_OFFLINE=1)")
    check_url(url, approved)
    limit = max_bytes or s.max_download_bytes
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0), follow_redirects=False,
                              headers={"User-Agent": user_agent or "HealthPulse/3.1 (+public-health research pilot)"}) as cli:
                target, hops = url, 0
                while True:
                    with cli.stream("GET", target) as r:
                        if r.status_code in (301, 302, 303, 307, 308):
                            target = str(httpx.URL(target).join(r.headers["location"]))
                            check_url(target, approved)
                            hops += 1
                            if hops > 5:
                                raise FetchError("too many redirects")
                            continue
                        if r.status_code == 429 or r.status_code >= 500:
                            raise FetchError(f"HTTP {r.status_code}")
                        if r.status_code >= 400:
                            raise FetchError(f"HTTP {r.status_code} (not retried)")
                        if r.status_code in (202, 204):  # bot-challenge / empty "accepted" pages are not data
                            raise FetchError(f"HTTP {r.status_code} with no data (not retried)")
                        total = int(r.headers.get("content-length") or 0)
                        if total and total > limit:
                            raise FetchError(f"response of {total} bytes exceeds {limit} bytes (not retried)")
                        n, head = 0, b""
                        with open(part, "wb") as f:
                            for chunk in r.iter_bytes(1 << 20):
                                if not head:
                                    head = chunk[:512]
                                    _sniff(head, r.headers.get("content-type", ""), expect)
                                n += len(chunk)
                                if n > limit:
                                    raise FetchError(f"response exceeds {limit} bytes (not retried)")
                                f.write(chunk)
                                if progress and total:
                                    progress(n / total)
                        os.replace(part, dest)
                        return {"status": r.status_code, "content_type": r.headers.get("content-type", ""),
                                "final_url": target, "bytes": n}
        except FetchError as e:
            last = e
            if "not retried" in str(e):
                break
        except (httpx.HTTPError, OSError) as e:
            last = e
        time.sleep(min(30.0, (2 ** attempt) + random.random()))
    part.unlink(missing_ok=True)
    raise FetchError(f"fetch failed for {url}: {last}")


def fetch_ranged_to_file(url: str, dest: Path, *, approved: tuple[str, ...] | list[str] | None = None, max_bytes: int | None = None,
                         expect: str | None = None, workers: int = 6, chunk_bytes: int = 8 << 20, retries: int = 4,
                         timeout: float = 120.0, progress=None, user_agent: str | None = None) -> dict:
    """Parallel HTTP Range download for large files from throttled hosts (HDX serves ~150 KB/s per connection).
    Same SSRF allowlist, redirect re-checks, size limit and HTML sniffing as `fetch_to_file`. The server must answer
    Range requests with 206 and a Content-Range total; otherwise this raises and the caller can fall back."""
    import re
    from concurrent.futures import ThreadPoolExecutor
    s = get_settings()
    if s.offline:
        raise FetchError("offline mode: network fetch disabled (HP_OFFLINE=1)")
    check_url(url, approved)
    limit = max_bytes or s.max_download_bytes
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    headers = {"User-Agent": user_agent or "HealthPulse/3.1 (+public-health research pilot)"}

    def resolve() -> tuple[str, int]:
        """Follow redirects (each hop re-checked) to the final URL and read the total size from a 1-byte Range probe."""
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0), follow_redirects=False, headers=headers) as cli:
            target, hops = url, 0
            while True:
                with cli.stream("GET", target, headers={"Range": "bytes=0-0"}) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        target = str(httpx.URL(target).join(r.headers["location"]))
                        check_url(target, approved)
                        hops += 1
                        if hops > 5:
                            raise FetchError("too many redirects")
                        continue
                    if r.status_code == 202:
                        raise FetchError("HTTP 202 bot challenge (the host is rate-limiting us; will wait and retry)")
                    if r.status_code != 206:
                        raise FetchError(f"HTTP {r.status_code}: server does not honour Range requests")
                    m = re.match(r"bytes 0-0/(\d+)", r.headers.get("content-range", ""))
                    if not m:
                        raise FetchError("no Content-Range total")
                    return target, int(m.group(1))

    import threading
    lock = threading.Lock()
    state = {"url": "", "gen": 0}

    def refresh(seen_gen: int) -> None:
        """Signed redirect targets (HDX -> S3) expire within minutes: take a fresh one, once per generation."""
        with lock:
            if state["gen"] != seen_gen:
                return
            last_err: Exception | None = None
            for attempt in range(4):
                try:
                    state["url"], _ = resolve()
                    state["gen"] += 1
                    return
                except FetchError as e:  # 202 bot challenge / transient: back off, then retry
                    last_err = e
                    time.sleep(15.0 * (attempt + 1))
            raise FetchError(f"could not refresh the download URL: {last_err}")

    final, total = "", 0
    for attempt in range(6):  # a 202 bot challenge clears after a few minutes without requests
        try:
            final, total = resolve()
            break
        except FetchError as e:
            if "bot challenge" not in str(e) or attempt == 5:
                raise
            time.sleep(40.0 * (attempt + 1))
    state["url"] = final
    if total > limit:
        raise FetchError(f"response of {total} bytes exceeds {limit} bytes (not retried)")
    spans = [(a, min(a + chunk_bytes, total) - 1) for a in range(0, total, chunk_bytes)]
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    done = [0]

    def get(span: tuple[int, int]) -> None:
        a, b = span
        last: Exception | None = None
        for attempt in range(retries + 2):
            gen = state["gen"]
            try:
                with httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0), follow_redirects=False, headers=headers) as cli:
                    with cli.stream("GET", state["url"], headers={"Range": f"bytes={a}-{b}"}) as r:
                        if r.status_code in (400, 401, 403):
                            refresh(gen)
                        if r.status_code != 206:
                            raise FetchError(f"HTTP {r.status_code} on range {a}-{b}")
                        off = a
                        for blk in r.iter_bytes(1 << 18):
                            if off == a and a == 0:
                                _sniff(blk[:512], r.headers.get("content-type", ""), expect)
                            os.pwrite(fd, blk, off)
                            off += len(blk)
                        if off != b + 1:
                            raise FetchError(f"short range {a}-{b}: got {off - a} bytes")
                done[0] += b - a + 1
                if progress:
                    progress(done[0] / total)
                return
            except (FetchError, httpx.HTTPError, OSError) as e:
                last = e
                time.sleep(min(20.0, (2 ** attempt) + random.random()))
        raise FetchError(f"range {a}-{b} failed: {last}")

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for f in [ex.submit(get, sp) for sp in spans]:
                f.result()
        os.close(fd)
        fd = -1
        os.replace(part, dest)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        part.unlink(missing_ok=True)
        raise
    return {"status": 200, "final_url": final.split("?")[0], "bytes": total, "ranged": True}


def _sniff(body: bytes, ctype: str, expect: str | None) -> None:
    head = body[:512].lstrip().lower()
    looks_html = head.startswith(b"<!doctype html") or head.startswith(b"<html")
    if expect in ("json", "csv", "zip", "geojson", "parquet", "tiff", "xlsx") and looks_html:
        raise FetchError("upstream returned HTML where data was expected (rejected)")
    if expect in ("zip", "xlsx") and not body.startswith(b"PK"):
        raise FetchError("expected a ZIP archive (bad magic)")
    if expect in ("json", "geojson") and head[:1] not in (b"{", b"["):
        raise FetchError("expected JSON")
    if expect == "parquet" and not body.startswith(b"PAR1"):
        raise FetchError("expected a Parquet file (bad magic)")
    if expect == "tiff" and body[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        raise FetchError("expected a GeoTIFF (bad magic)")


# ---------------------------------------------------------------- Bronze
def bronze_write(source_id: str, payloads: list[tuple[str, bytes | Path]], manifest: dict) -> dict:
    """Append-only snapshot directory data/raw/{source}/ingested_at=TS/{sha}.{ext} plus manifest.json.
    A payload may be bytes or a Path to a downloaded temp file (moved in without loading it into memory)."""
    s = get_settings()
    d = s.raw_dir / source_id / f"ingested_at={stamp()}"
    while d.exists():
        time.sleep(1.0)
        d = s.raw_dir / source_id / f"ingested_at={stamp()}"
    d.mkdir(parents=True)
    files = []
    for name, data in payloads:
        h = sha256_file(data) if isinstance(data, Path) else sha256_bytes(data)
        ext = name.rsplit(".", 1)[-1] if "." in name else "bin"
        p = d / f"{h}.{ext}"
        if p.exists():  # identical bytes already stored in this snapshot (content-addressed)
            if isinstance(data, Path):
                data.unlink(missing_ok=True)
        elif isinstance(data, Path):
            shutil.move(str(data), p)
        else:
            p.write_bytes(data)
        os.chmod(p, 0o444)
        files.append({"name": name, "sha256": h, "bytes": p.stat().st_size, "path": str(p.relative_to(s.data_dir))})
    combined = sha256_bytes("".join(f["sha256"] for f in files).encode())
    man = {**manifest, "files": files, "sha256": combined, "bronze_dir": str(d.relative_to(s.data_dir))}
    write_json_atomic(d / "manifest.json", man)
    return man


def bronze_write_file(source_id: str, src: Path, name: str, manifest: dict) -> dict:
    """Large uploads (millions of rows) are moved into Bronze without loading them in memory."""
    s = get_settings()
    d = s.raw_dir / source_id / f"ingested_at={stamp()}"
    d.mkdir(parents=True, exist_ok=False)
    h = sha256_file(src)
    ext = name.rsplit(".", 1)[-1] if "." in name else "bin"
    dst = d / f"{h}.{ext}"
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o444)
    files = [{"name": name, "sha256": h, "bytes": dst.stat().st_size, "path": str(dst.relative_to(s.data_dir))}]
    man = {**manifest, "files": files, "sha256": h, "bronze_dir": str(d.relative_to(s.data_dir))}
    write_json_atomic(d / "manifest.json", man)
    return man


def bronze_path(rel: str) -> Path:
    return get_settings().data_dir / rel


# ---------------------------------------------------------------- Silver
def silver_publish(table: str, source_id: str, vintage_id: str, df: pl.DataFrame) -> tuple[str, str]:
    """Write to a temp dir then rename: publication is atomic, a failed run never leaves a half-written vintage."""
    s = get_settings()
    final = s.silver_dir / table / f"source_id={source_id}" / f"vintage_id={vintage_id}"
    tmp = final.with_name(final.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    p = tmp / "part-0.parquet"
    df.write_parquet(p, compression="zstd", statistics=True)
    digest = sha256_file(p)
    os.replace(tmp, final)
    return str((final / "part-0.parquet").relative_to(s.data_dir)), digest


def data_path(rel: str) -> Path:
    return get_settings().data_dir / rel


def artifact_dir(*parts: str) -> Path:
    p = get_settings().artifacts_dir.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_parquet_atomic(df: pl.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    df.write_parquet(tmp, compression="zstd", statistics=True)
    os.replace(tmp, path)
    return sha256_file(path)


def rel_artifact(p: Path) -> str:
    return str(p.relative_to(get_settings().artifacts_dir))


def abs_artifact(rel: str) -> Path:
    p = (get_settings().artifacts_dir / rel).resolve()
    if get_settings().artifacts_dir not in p.parents and p != get_settings().artifacts_dir:
        raise UnsafeURL("artifact path escapes the artifact store")
    return p


def manifest_base(source_id: str, url: str | None, **kw: Any) -> dict[str, Any]:
    from .util import iso
    now = iso(utcnow())
    return {"source_id": source_id, "source_url": url, "retrieved_at": now, "available_at": now,
            "schema_version": "1.0", "status": "downloaded", **kw}
