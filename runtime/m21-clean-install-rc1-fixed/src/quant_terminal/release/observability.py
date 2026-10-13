"""Bounded local request metrics and safe structured operational events."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import Counter, deque
from contextlib import closing
from pathlib import Path

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from quant_terminal.logging import write_event


def _linked_path(path: Path) -> bool:
    return any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))


class RuntimeMonitor:
    def __init__(self, directory: Path, *, max_log_bytes: int = 1_000_000) -> None:
        self.directory, self.max_log_bytes = directory, max_log_bytes
        self.lock = threading.Lock()
        self.requests = 0
        self.statuses: Counter[str] = Counter()
        self.durations: deque[int] = deque(maxlen=256)
        self.errors: deque[dict[str, object]] = deque(maxlen=32)
        self.log_failures = 0

    @property
    def healthy(self) -> bool:
        with self.lock:
            return self.log_failures == 0

    def record(self, status: int, elapsed_ns: int) -> None:
        fields: dict[str, object] = {"status": status, "duration_ns": elapsed_ns}
        with self.lock:
            self.requests += 1
            self.statuses[f"{status // 100}xx"] += 1
            self.durations.append(elapsed_ns)
            if status >= 500:
                self.errors.append({"category": "http_server_error", "status": status})
            try:
                if _linked_path(self.directory):
                    raise OSError("Log directory cannot traverse links")
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / "runtime.jsonl"
                rotated = self.directory / "runtime.previous.jsonl"
                if any(p.is_symlink() or p.is_junction() for p in (path, rotated)):
                    raise OSError("Log files cannot be links")
                if path.exists() and path.stat().st_size >= self.max_log_bytes:
                    path.replace(rotated)
                with path.open("a", encoding="utf-8") as stream:
                    write_event(stream, "http.request", fields)
            except OSError:
                # Health latches failure; never log exception text or request data.
                self.log_failures += 1

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            samples = sorted(self.durations)
            return {
                "scope": "this_process",
                "requests": self.requests,
                "status_classes": dict(self.statuses),
                "latency_samples": len(samples),
                "latency_p95_ns": samples[(len(samples) - 1) * 95 // 100] if samples else None,
                "recent_server_errors": tuple(self.errors),
                "log_failures": self.log_failures,
            }


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, monitor: RuntimeMonitor) -> None:
        self.app, self.monitor = app, monitor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started, status = time.monotonic_ns(), 500

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            self.monitor.record(status, max(0, time.monotonic_ns() - started))


def storage_health(root: Path) -> dict[str, object]:
    """Read-only SQLite integrity checks, bounded by the documented runtime DB count."""
    if _linked_path(root):
        return {"healthy": False, "reason": "unsafe_storage_path"}
    paths = [root / "jobs.sqlite", root / "experiments.sqlite3"]
    paper = root / "paper"
    if paper.exists() and (paper.is_symlink() or paper.is_junction()):
        return {"healthy": False, "reason": "unsafe_storage_path"}
    paths.extend(sorted(paper.glob("*.sqlite3")))
    if len(paths) > 66:
        return {"healthy": False, "reason": "database_count_exceeds_health_budget"}
    checked = 0
    try:
        for path in paths:
            if not path.exists():
                continue
            if path.is_symlink() or path.is_junction():
                return {"healthy": False, "reason": "unsafe_storage_path"}
            with closing(
                sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            ) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    return {"healthy": False, "reason": "database_integrity"}
            checked += 1
    except (sqlite3.DatabaseError, OSError):
        return {"healthy": False, "reason": "storage_unavailable_or_corrupt"}
    return {"healthy": True, "databases_checked": checked}
