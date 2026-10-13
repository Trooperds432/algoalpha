"""Durable bounded local research jobs, with one scheduler per state directory."""

from __future__ import annotations

import builtins
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Self, cast
from uuid import uuid4

from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import canonical_json
from quant_terminal.sandbox.admission import assert_trusted_request

KINDS = frozenset({"backtest", "optimize", "walk-forward", "monte-carlo", "robustness"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})


class JobNotFound(DomainError):
    """No durable job with this identity exists."""


class QueueFull(DomainError):
    """The configured pending job budget is exhausted."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    def __init__(self, root: Path, *, capacity: int = 64) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 1024:
            raise DomainError("Job capacity must be in [1, 1024]")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path, self.capacity = self.root / "jobs.sqlite", capacity
        with self._connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise DomainError("Unsupported job database schema version")
            db.executescript(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                "status TEXT NOT NULL, request TEXT NOT NULL, result TEXT, error TEXT, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "job_id TEXT NOT NULL REFERENCES jobs(id), payload TEXT NOT NULL);"
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        for field in ("request", "result", "error"):
            result[field] = None if result[field] is None else json.loads(result[field])
        return result

    def submit(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        if kind not in KINDS:
            raise DomainError("Unsupported research operation")
        encoded = canonical_json(payload)
        if len(encoded) > 2_000_000:
            raise DomainError("Research request exceeds 2 MB")
        snapshot = json.loads(encoded)
        assert_trusted_request(snapshot)
        return self._enqueue(kind, snapshot)

    def _enqueue(
        self,
        kind: str,
        payload: Mapping[str, object],
        *,
        decision: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """Trusted scheduler seam; each mode must validate before this call."""
        encoded = canonical_json(payload)
        if len(encoded) > 2_000_000:
            raise DomainError("Research request exceeds 2 MB")
        identity, stamp = uuid4().hex, _now()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')"
            ).fetchone()[0]
            if count >= self.capacity:
                raise QueueFull("Research queue is full")
            if decision is not None:
                decision(identity)
            db.execute(
                "INSERT INTO jobs VALUES (?,?, 'queued', ?,NULL,NULL,?,?)",
                (identity, kind, encoded.decode(), stamp, stamp),
            )
        return self.get(identity)

    def get(self, identity: str) -> dict[str, object]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise JobNotFound("Job not found")
        return self._row(row)

    def list(self, limit: int = 100) -> list[dict[str, object]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise DomainError("Job list limit must be in [1, 1000]")
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC,id LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(row) for row in rows]

    def claim(self) -> dict[str, object] | None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?", (_now(), row[0]))
        return self.get(row[0])

    def has_queued(self) -> bool:
        """Queue readiness is independent of the display page limit."""
        with self._connection() as db:
            return bool(
                db.execute("SELECT EXISTS(SELECT 1 FROM jobs WHERE status='queued')").fetchone()[0]
            )

    def finish(
        self, identity: str, status: str, *, result: object = None, error: object = None
    ) -> None:
        if status not in TERMINAL:
            raise DomainError("Expected terminal job state")
        with self._connection() as db:
            db.execute(
                "UPDATE jobs SET status=?,result=?,error=?,updated_at=? WHERE id=? "
                "AND status IN ('running','cancelling') "
                "AND (status!='cancelling' OR ?!='succeeded')",
                (
                    status,
                    canonical_json(result).decode(),
                    canonical_json(error).decode(),
                    _now(),
                    identity,
                    status,
                ),
            )

    def cancel(
        self,
        identity: str,
        *,
        decision: Callable[[Mapping[str, object], str], None] | None = None,
    ) -> dict[str, object]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise JobNotFound("Job not found")
            status = row["status"]
            target = (
                "cancelled"
                if status == "queued"
                else "cancelling"
                if status == "running"
                else status
            )
            if decision is not None:
                decision(self._row(row), target)
            if target != status:
                db.execute(
                    "UPDATE jobs SET status=?,updated_at=? WHERE id=?", (target, _now(), identity)
                )
        return self.get(identity)

    def publish_after_cleanup(
        self, identity: str, publish: Callable[[], Mapping[str, object]]
    ) -> dict[str, object]:
        """Linearize completed publication against independent durable cancel.

        Only a trusted lifecycle owner may call this after confirmed cleanup.
        BEGIN IMMEDIATE excludes another process's cancel write until this
        completion decision commits. A previously committed cancel never calls
        the publisher. Publication writes a separate immutable experiment store.
        """
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise JobNotFound("Job not found")
            if row["status"] == "cancelling":
                db.execute(
                    "UPDATE jobs SET status='cancelled',error=?,updated_at=? WHERE id=?",
                    (
                        canonical_json(
                            {"type": "Cancelled", "message": "Cancelled before publication"}
                        ).decode(),
                        _now(),
                        identity,
                    ),
                )
            elif row["status"] == "running":
                result = publish()
                encoded = canonical_json(result).decode()
                db.execute(
                    "UPDATE jobs SET status='succeeded',result=?,error=NULL,updated_at=? "
                    "WHERE id=?",
                    (encoded, _now(), identity),
                )
                db.execute(
                    "INSERT INTO events(job_id,payload) VALUES (?,?)",
                    (
                        identity,
                        canonical_json(
                            {"event": "registered_run", "run_id": result["run_id"]}
                        ).decode(),
                    ),
                )
            elif row["status"] not in TERMINAL:
                raise DomainError("Job is not ready for completed publication")
            current = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            assert current is not None
            return self._row(current)

    def recover(self) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE jobs SET status='interrupted',error=?,updated_at=? "
                "WHERE status IN ('running','cancelling')",
                (
                    canonical_json({"type": "Restart", "message": "Scheduler restarted"}).decode(),
                    _now(),
                ),
            )

    def record(self, identity: str, payload: Mapping[str, object]) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO events(job_id,payload) VALUES (?,?)",
                (identity, canonical_json(payload).decode()),
            )

    def events(self, identity: str, *, after: int = 0, limit: int = 100) -> builtins.list[object]:
        self.get(identity)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise DomainError("Invalid event page")
        with self._connection() as db:
            rows = db.execute(
                "SELECT seq,payload FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                (identity, after, limit),
            ).fetchall()
        return [{"sequence": row[0], "event": json.loads(row[1])} for row in rows]


class SchedulerLease:
    """OS-released lock; an abandoned lock file does not prevent crash recovery."""

    def __init__(self, path: Path) -> None:
        self.path, self.handle = path, cast(BinaryIO | None, None)

    def acquire(self) -> None:
        handle = self.path.open("a+b")
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise DomainError("A scheduler already owns this state directory; use its API") from exc
        self.handle = handle

    def release(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class JobManager:
    """Bounded trusted worker processes; this is not an adversarial sandbox."""

    def __init__(
        self, root: Path, project_root: Path, *, workers: int = 2, timeout: int = 300
    ) -> None:
        if type(workers) is not int or not 1 <= workers <= 3:
            raise DomainError("Worker count must be in [1, 3]")
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise DomainError("Job timeout must be in [1, 3600] seconds")
        self.store = JobStore(root)
        self.project_root = project_root.resolve()
        if self.store.root.is_relative_to(self.project_root):
            raise DomainError("Runtime state must be outside the project source tree")
        self.workers, self.timeout = workers, timeout
        self.lease = SchedulerLease(self.store.root / "scheduler.lock")
        self.stop = threading.Event()
        self.active: dict[str, tuple[subprocess.Popen[bytes], float]] = {}
        self.thread: threading.Thread | None = None
        self.failure: str | None = None

    @property
    def healthy(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and self.failure is None

    def submit(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        if not self.healthy:
            raise DomainError("Research scheduler is unavailable")
        return self.store.submit(kind, payload)

    def __enter__(self) -> Self:
        self.lease.acquire()
        try:
            self.store.recover()
            self.thread = threading.Thread(
                target=self._loop, name="research-scheduler", daemon=True
            )
            self.thread.start()
        except BaseException:
            self.lease.release()
            raise
        return self

    def __exit__(self, *args: object) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise DomainError("Scheduler shutdown incomplete; exclusive lease remains held")

    def _launch(self, job: dict[str, object]) -> None:
        identity = str(job["id"])
        allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "LANG", "LC_ALL"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        environment["PYTHONPATH"] = str(self.project_root / "src")
        environment["PYTHONUTF8"] = "1"
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "quant_terminal.application.worker",
                    str(self.store.root),
                    str(self.project_root),
                    identity,
                    str(os.getpid()),
                ],
                cwd=self.project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as exc:
            self.store.finish(
                identity,
                "failed",
                error={"type": type(exc).__name__, "message": "Worker could not start"},
            )
            return
        self.active[identity] = process, time.monotonic()

    def _loop(self) -> None:
        try:
            while not self.stop.is_set():
                for identity, (process, started) in list(self.active.items()):
                    status = self.store.get(identity)["status"]
                    expired = time.monotonic() - started > self.timeout
                    if status == "cancelling" or expired:
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=5)
                        self.store.finish(
                            identity,
                            "cancelled" if status == "cancelling" else "failed",
                            error={
                                "type": "Cancelled" if status == "cancelling" else "Timeout",
                                "message": "Worker terminated",
                            },
                        )
                    if process.poll() is not None:
                        if self.store.get(identity)["status"] == "running":
                            self.store.finish(
                                identity,
                                "failed",
                                error={
                                    "type": "WorkerExit",
                                    "message": "Worker exited without a result",
                                },
                            )
                        del self.active[identity]
                while len(self.active) < self.workers and not self.stop.is_set():
                    job = self.store.claim()
                    if job is None:
                        break
                    self._launch(job)
                self.stop.wait(0.1)
        except Exception as exc:
            self.failure = type(exc).__name__
        finally:
            try:
                while self.active:
                    for identity, (process, _) in list(self.active.items()):
                        try:
                            if process.poll() is None:
                                process.kill()
                            process.wait(timeout=1)
                        except Exception as exc:
                            self.failure = type(exc).__name__
                        if process.poll() is not None:
                            try:
                                self.store.finish(
                                    identity,
                                    "interrupted",
                                    error={"type": "Shutdown", "message": "Scheduler stopped"},
                                )
                            except Exception as exc:
                                self.failure = type(exc).__name__
                            del self.active[identity]
                    if self.active:
                        # Keep the lease until process death is confirmed. __exit__
                        # reports an incomplete shutdown while this thread retries.
                        time.sleep(0.1)
            finally:
                if not self.active:
                    self.lease.release()
