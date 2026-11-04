"""Durable job queue on the metadata database.

The API only enqueues (returns 202 + job_id in milliseconds). A separate worker supervisor process claims jobs and
runs each one in its own child process, so a job can be hard-killed on timeout or cancellation without touching the
API. Every job records stages, progress, logs and its result; a retry is a new job linked by `retry_of`, never a
reset of the original audit record.

States: queued -> running -> (succeeded | succeeded_with_warnings | quarantined | failed | canceled | timed_out)
"""
from __future__ import annotations

import multiprocessing as mp
import os
import signal
import socket
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from . import db
from .util import dumps, iso, loads, new_id, parse_dt, utcnow

TERMINAL = {"succeeded", "succeeded_with_warnings", "quarantined", "failed", "canceled", "timed_out"}
HANDLERS: dict[str, Callable[["JobContext", dict], dict]] = {}


def handler(job_type: str):
    def deco(fn):
        HANDLERS[job_type] = fn
        return fn
    return deco


class Canceled(Exception):
    pass


class JobWarning(Exception):
    """Raise at the end of a job to finish as succeeded_with_warnings / quarantined with a result."""

    def __init__(self, status: str, result: dict):
        super().__init__(status)
        self.status, self.result = status, result


def enqueue(job_type: str, payload: dict, *, target_type: str | None = None, target_id: str | None = None,
            idempotency_key: str | None = None, actor_id: str | None = None, retry_of: str | None = None,
            priority: int = 5, timeout_s: int = 1800) -> dict:
    if idempotency_key:
        existing = db.q1("SELECT * FROM jobs WHERE idempotency_key=?", idempotency_key)
        if existing and existing["status"] not in {"failed", "canceled", "timed_out"}:
            return public(existing)
        if existing:  # a failed attempt with the same key: the new attempt gets a fresh key suffix
            idempotency_key = f"{idempotency_key}#{new_id('a')}"
    attempt = 1
    if retry_of:
        prev = db.q1("SELECT attempt_no FROM jobs WHERE job_id=?", retry_of)
        attempt = (prev["attempt_no"] if prev else 0) + 1
    job_id = new_id("job")
    now = iso(utcnow())
    db.insert("jobs", {
        "job_id": job_id, "job_type": job_type, "target_type": target_type, "target_id": target_id,
        "status": "queued", "stage": "queued", "progress": 0.0, "payload_json": dumps(payload),
        "idempotency_key": idempotency_key, "retry_of": retry_of, "attempt_no": attempt, "priority": priority,
        "created_by": actor_id, "created_at": now, "queued_at": now, "timeout_s": timeout_s, "stages_json": "[]",
    })
    return get(job_id)


def get(job_id: str) -> dict | None:
    row = db.q1("SELECT * FROM jobs WHERE job_id=?", job_id)
    return public(row) if row else None


def public(row: dict) -> dict:
    r = dict(row)
    for k in ("payload_json", "result_json", "stages_json"):
        r[k.replace("_json", "")] = loads(r.pop(k)) if r.get(k) else ([] if k == "stages_json" else None)
    r.pop("idempotency_key", None)
    if r.get("started_at"):
        end = parse_dt(r.get("finished_at")) or utcnow()
        r["elapsed_s"] = round((end - parse_dt(r["started_at"])).total_seconds(), 1)
    return r


def logs(job_id: str, after: int = 0, limit: int = 500) -> list[dict]:
    return db.q("SELECT seq, ts, level, stage, message FROM job_log WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                job_id, after, limit)


def request_cancel(job_id: str) -> dict | None:
    row = db.q1("SELECT status FROM jobs WHERE job_id=?", job_id)
    if not row:
        return None
    if row["status"] == "queued":
        db.ex("UPDATE jobs SET status='canceled', stage='canceled', finished_at=?, cancel_requested=1 WHERE job_id=? AND status='queued'",
              iso(utcnow()), job_id)
    elif row["status"] == "running":
        db.ex("UPDATE jobs SET cancel_requested=1 WHERE job_id=?", job_id)
    return get(job_id)


def list_jobs(*, job_type: str | None = None, status: str | None = None, target_id: str | None = None,
              limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    where, args = [], []
    if job_type:
        where.append("job_type IN (%s)" % ",".join("?" * len(job_type.split(","))))
        args += job_type.split(",")
    if status:
        where.append("status IN (%s)" % ",".join("?" * len(status.split(","))))
        args += status.split(",")
    if target_id:
        where.append("target_id=?")
        args.append(target_id)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.q1(f"SELECT COUNT(*) n FROM jobs {w}", *args)["n"]
    rows = db.q(f"SELECT * FROM jobs {w} ORDER BY created_at DESC LIMIT ? OFFSET ?", *args, limit, offset)
    return [public(r) for r in rows], total


# ---------------------------------------------------------------- execution context (inside the child process)
@dataclass
class JobContext:
    job_id: str
    job_type: str
    actor_id: str | None
    stages: list[dict] = field(default_factory=list)
    _last_beat: float = 0.0

    def log(self, message: str, level: str = "info", stage: str | None = None) -> None:
        db.insert("job_log", {"job_id": self.job_id, "ts": iso(utcnow()), "level": level,
                              "stage": stage or (self.stages[-1]["name"] if self.stages else None), "message": message[:4000]})

    def stage(self, name: str, progress: float | None = None, **counts: Any) -> None:
        now = iso(utcnow())
        if self.stages and self.stages[-1].get("finished_at") is None:
            self.stages[-1]["finished_at"] = now
            self.stages[-1]["status"] = "done"
        self.stages.append({"name": name, "started_at": now, "finished_at": None, "status": "running", **counts})
        self._write(stage=name, progress=progress)
        self.log(f"stage {name} started", stage=name)

    def counts(self, **counts: Any) -> None:
        if self.stages:
            self.stages[-1].update(counts)
            self._write()

    def progress(self, value: float, message: str | None = None) -> None:
        self._write(progress=max(0.0, min(1.0, value)))
        if message:
            self.log(message)

    def check_cancel(self) -> None:
        row = db.q1("SELECT cancel_requested FROM jobs WHERE job_id=?", self.job_id)
        if row and row["cancel_requested"]:
            raise Canceled()

    def _write(self, stage: str | None = None, progress: float | None = None) -> None:
        vals: dict[str, Any] = {"stages_json": dumps(self.stages), "heartbeat_at": iso(utcnow())}
        if stage is not None:
            vals["stage"] = stage
        if progress is not None:
            vals["progress"] = progress
        db.update("jobs", {"job_id": self.job_id}, vals)

    def close_stages(self, status: str = "done") -> None:
        if self.stages and self.stages[-1].get("finished_at") is None:
            self.stages[-1]["finished_at"] = iso(utcnow())
            self.stages[-1]["status"] = status
        self._write()


def run_job_inline(job_id: str) -> dict:
    """Execute one job in the current process (worker child, tests and the CLI use this)."""
    from .. import handlers  # noqa: F401  (registers every handler)

    row = db.q1("SELECT * FROM jobs WHERE job_id=?", job_id)
    if row is None:
        raise KeyError(job_id)
    if row["status"] == "queued":
        db.ex("UPDATE jobs SET status='running', started_at=?, heartbeat_at=? WHERE job_id=?", iso(utcnow()), iso(utcnow()), job_id)
    ctx = JobContext(job_id, row["job_type"], row["created_by"])
    fn = HANDLERS.get(row["job_type"])
    status, result, error = "succeeded", None, None
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job type {row['job_type']}")
        result = fn(ctx, loads(row["payload_json"]) or {})
        ctx.close_stages()
    except Canceled:
        status, error = "canceled", "canceled by request"
        ctx.close_stages("canceled")
        ctx.log("job canceled", level="warning")
    except JobWarning as w:
        status, result = w.status, w.result
        ctx.close_stages("warning")
    except Exception as e:  # noqa: BLE001 — the job record must capture every failure
        status, error = "failed", f"{type(e).__name__}: {e}"
        ctx.close_stages("failed")
        ctx.log(traceback.format_exc(limit=8), level="error")
    db.update("jobs", {"job_id": job_id}, {
        "status": status, "stage": status, "progress": 1.0 if status.startswith("succeeded") else row["progress"],
        "result_json": dumps(result) if result is not None else None, "error": error, "finished_at": iso(utcnow()),
    })
    return get(job_id)


# ---------------------------------------------------------------- worker supervisor
def _claim(worker_id: str, job_types: set[str] | None) -> str | None:
    with db.tx() as c:
        where = "status='queued'"
        args: list[Any] = []
        if job_types:
            where += " AND job_type IN (%s)" % ",".join("?" * len(job_types))
            args += sorted(job_types)
        r = c.execute(f"SELECT job_id FROM jobs WHERE {where} ORDER BY priority, created_at LIMIT 1", args).fetchone()
        if not r:
            return None
        now = iso(utcnow())
        c.execute("UPDATE jobs SET status='running', stage='starting', started_at=?, heartbeat_at=?, worker_id=? WHERE job_id=?",
                  (now, now, worker_id, r[0]))
        return r[0]


def _child(job_id: str) -> None:
    db.close()
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        os.nice(5)
    except OSError:
        pass
    run_job_inline(job_id)


def _finish_killed(job_id: str, status: str, error: str) -> None:
    db.ex("UPDATE jobs SET status=?, stage=?, error=?, finished_at=? WHERE job_id=? AND status='running'",
          status, status, error, iso(utcnow()), job_id)
    db.insert("job_log", {"job_id": job_id, "ts": iso(utcnow()), "level": "error", "stage": status, "message": error})


def reap_orphans(max_silence_s: int = 180) -> int:
    """Running jobs whose heartbeat stopped (worker crashed) are failed, never left spinning forever."""
    n = 0
    for r in db.q("SELECT job_id, heartbeat_at FROM jobs WHERE status='running'"):
        hb = parse_dt(r["heartbeat_at"])
        if hb and (utcnow() - hb).total_seconds() > max_silence_s:
            _finish_killed(r["job_id"], "failed", "worker lost (no heartbeat)")
            n += 1
    return n


def run_worker(concurrency: int = 2, job_types: set[str] | None = None, poll_s: float = 0.5,
               stop_after_idle_s: float | None = None) -> None:
    db.migrate()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    ctx = mp.get_context("spawn")
    running: dict[str, tuple[Any, float, int]] = {}
    reap_orphans()
    idle_since = time.time()
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not stop["flag"]:
        for jid, (proc, started, timeout) in list(running.items()):
            if not proc.is_alive():
                proc.join()
                row = db.q1("SELECT status FROM jobs WHERE job_id=?", jid)
                if row and row["status"] == "running":
                    _finish_killed(jid, "failed", f"worker child exited with code {proc.exitcode}")
                running.pop(jid)
                continue
            row = db.q1("SELECT cancel_requested FROM jobs WHERE job_id=?", jid)
            if row and row["cancel_requested"] and time.time() - started > 0:
                # cooperative cancel first; hard kill if the job ignores it for 10 s
                if not hasattr(proc, "_cancel_at"):
                    proc._cancel_at = time.time()  # type: ignore[attr-defined]
                elif time.time() - proc._cancel_at > 10:  # type: ignore[attr-defined]
                    proc.kill()
                    proc.join()
                    _finish_killed(jid, "canceled", "canceled (worker terminated the job)")
                    running.pop(jid)
                    continue
            if time.time() - started > timeout:
                proc.kill()
                proc.join()
                _finish_killed(jid, "timed_out", f"exceeded timeout of {timeout}s")
                running.pop(jid)
        while len(running) < concurrency and not stop["flag"]:
            jid = _claim(worker_id, job_types)
            if not jid:
                break
            t = db.q1("SELECT timeout_s FROM jobs WHERE job_id=?", jid)["timeout_s"]
            p = ctx.Process(target=_child, args=(jid,), daemon=False)
            p.start()
            running[jid] = (p, time.time(), int(t))
            idle_since = time.time()
        if not running and stop_after_idle_s is not None and time.time() - idle_since > stop_after_idle_s:
            break
        time.sleep(poll_s)
    for jid, (proc, _, _) in running.items():
        proc.terminate()
        proc.join(5)
        _finish_killed(jid, "failed", "worker shut down during the job")
