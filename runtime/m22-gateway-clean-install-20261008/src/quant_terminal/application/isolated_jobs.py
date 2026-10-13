"""Bounded operator-driven isolated queue; no public submission authority.

An operator calls run_next (optionally on its own trusted thread). Each job gets
exclusive runtime ownership and cleanup before publication or terminal state.
The ordinary trusted queue is stored separately and cannot admit this job kind.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, Self, cast

from quant_terminal.application.isolated_runtime import CancellableBackend, IsolatedResearchRuntime
from quant_terminal.application.jobs import TERMINAL, JobStore, SchedulerLease
from quant_terminal.application.runtime import _object
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import ExperimentConfig
from quant_terminal.sandbox.protocol import SandboxResponse
from quant_terminal.sandbox.source_package import SourcePackage


class RuntimeOwner(Protocol):
    @property
    def backend(self) -> CancellableBackend: ...

    def start(self) -> None: ...

    def ensure_available(self) -> None: ...

    def close(self) -> None: ...


class _AdmissionBackend:
    """Pinned ready identity for parsing; never an execution backend."""

    def __init__(self, identity: str) -> None:
        self._identity = identity

    def identity(self) -> str:
        return self._identity

    def execute(
        self,
        request_blob: bytes,
        cancel: threading.Event | None = None,
        *,
        expected_identity: str | None = None,
    ) -> SandboxResponse:
        raise DomainError("Admission cannot execute source")


class IsolatedJobScheduler:
    def __init__(
        self,
        root: Path,
        project_root: Path,
        owner_factory: Callable[[], RuntimeOwner],
        *,
        capacity: int = 8,
        timeout: float = 120,
        research_root: Path | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 1 <= timeout <= 300
        ):
            raise DomainError("Isolated job timeout must be in [1,300] seconds")
        self.root, self.project_root = Path(root).resolve(), Path(project_root).resolve(strict=True)
        self.research_root = (
            self.root / "isolated-research"
            if research_root is None
            else Path(research_root).resolve()
        )
        self.store = JobStore(self.root / "isolated-jobs", capacity=capacity)
        self.lease = SchedulerLease(self.store.root / "isolated-scheduler.lock")
        self.owner_factory, self.timeout = owner_factory, timeout
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._owner: RuntimeOwner | None = None
        self._identity: str | None = None
        self._active: tuple[str, threading.Event] | None = None
        self._started = False
        self._stopping = False
        self.failure: str | None = None

    def _new_owner(self) -> RuntimeOwner:
        owner = self.owner_factory()
        self._owner = owner  # Retain failed-start ownership for explicit cleanup.
        owner.start()
        self._identity = owner.backend.identity()
        return owner

    def __enter__(self) -> Self:
        with self._lock:
            if self._started or self._stopping:
                raise DomainError("Isolated scheduler is one-use")
            self.lease.acquire()
            try:
                self._new_owner()
                # Startup reconcile is proved by the owner before interrupted
                # source gets a terminal state. Interrupted source is not rerun.
                self.store.recover()
                self._started = True
            except BaseException:
                self.failure = "startup_failed"
                if self._owner is not None:
                    self._owner.close()
                    self._owner = None
                self.lease.release()
                raise
        return self

    def _ready_owner(self) -> RuntimeOwner:
        if not self._started or self._stopping or self.failure is not None:
            raise DomainError("Isolated scheduler unavailable")
        owner = self._owner or self._new_owner()
        owner.ensure_available()
        return owner

    def submit(self, source_package: object, payload: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            owner = self._ready_owner()
            package = SourcePackage.from_payload(source_package)
            assert self._identity is not None
            # No source is persisted until bounded package/common data/financial
            # parsing succeeds and current owner readiness is checked again.
            runtime = IsolatedResearchRuntime(
                self.research_root,
                self.project_root,
                _AdmissionBackend(self._identity),
                package,
            )
            config = runtime.prepare(payload)
            owner.ensure_available()
            return self.store._enqueue(
                "isolated-backtest",
                {"source_package": package.to_payload(), "config": config.to_dict()},
            )

    def cancel(self, identity: str) -> dict[str, object]:
        with self._lock:
            job = self.store.cancel(identity)
            if self._active is not None and self._active[0] == identity:
                self._active[1].set()
            return job

    def run_next(self) -> dict[str, object] | None:
        if not self._operation.acquire(blocking=False):
            raise DomainError("Isolated scheduler busy")
        owner: RuntimeOwner | None = None
        job: dict[str, object] | None = None
        done = threading.Event()
        expired = threading.Event()
        timer: threading.Timer | None = None
        watcher: threading.Thread | None = None
        runtime: IsolatedResearchRuntime | None = None
        prepared = None
        failed = False
        try:
            with self._lock:
                owner = self._ready_owner()
                job = self.store.claim()
                if job is None:
                    return None
                identity = str(job["id"])
                cancel = threading.Event()
                self._active = identity, cancel

            def timeout() -> None:
                expired.set()
                cancel.set()

            def watch() -> None:
                while not done.wait(0.05):
                    try:
                        if self.store.get(identity)["status"] == "cancelling":
                            cancel.set()
                    except Exception:
                        cancel.set()

            timer = threading.Timer(self.timeout, timeout)
            timer.daemon = True
            timer.start()
            watcher = threading.Thread(target=watch, daemon=True, name="isolated-cancellation")
            watcher.start()
            try:
                if job["kind"] != "isolated-backtest":
                    raise DomainError("Wrong isolated queue kind")
                packet = _object(
                    job["request"], {"source_package", "config"}, {"source_package", "config"}
                )
                package = SourcePackage.from_payload(packet["source_package"])
                config = ExperimentConfig.from_dict(cast(Mapping[str, object], packet["config"]))
                runtime = IsolatedResearchRuntime(
                    self.research_root,
                    self.project_root,
                    owner.backend,
                    package,
                    cancel=cancel,
                )
                self.store.record(
                    identity, {"event": "isolated_started", "source_sha256": package.source_sha256}
                )
                prepared = runtime._execute(config, "run-" + identity)
            except Exception:
                failed = True
            # Even a failed boundary cannot finalize its job until cleanup is
            # confirmed. Failure here retains owner, active job and OS lease.
            owner.close()
            with self._lock:
                self._owner = None
                done.set()
                timer.cancel()
                status = self.store.get(identity)["status"]
                if self._stopping:
                    terminal = "interrupted"
                elif status == "cancelling":
                    terminal = "cancelled"
                elif expired.is_set() or failed or cancel.is_set():
                    terminal = "failed"
                else:
                    terminal = "succeeded"
                if terminal == "succeeded":
                    assert runtime is not None and prepared is not None
                    manifest, result, artifacts = prepared
                    try:
                        self.store.publish_after_cleanup(
                            identity,
                            lambda: runtime._register(manifest, result, artifacts).to_dict(),
                        )
                    except Exception:
                        self.store.finish(
                            identity,
                            "failed",
                            error={
                                "type": "Publication",
                                "message": "Isolated experiment publication failed",
                            },
                        )
                else:
                    self.store.finish(
                        identity,
                        terminal,
                        error={
                            "type": "Timeout" if expired.is_set() else "IsolatedJob",
                            "message": "Isolated job stopped after confirmed cleanup",
                        },
                    )
                completed = self.store.get(identity)
                if completed["status"] not in TERMINAL:
                    raise DomainError("Job completion unconfirmed; exclusive lease retained")
                self._active = None
                return completed
        except Exception:
            with self._lock:
                self.failure = "isolated_lifecycle_incomplete"
            raise
        finally:
            done.set()
            if timer is not None:
                timer.cancel()
            if watcher is not None:
                watcher.join(timeout=2)
            self._operation.release()

    def close(self, *, timeout: float = 15) -> None:
        with self._lock:
            self._stopping = True
            if self._active is not None:
                self._active[1].set()
        if not self._operation.acquire(timeout=timeout):
            raise DomainError("Isolated shutdown incomplete; exclusive lease retained")
        try:
            with self._lock:
                if self._owner is not None:
                    self._owner.close()
                    self._owner = None
                if self._active is not None:
                    self.store.finish(
                        self._active[0],
                        "interrupted",
                        error={
                            "type": "Shutdown",
                            "message": "Confirmed cleanup after lifecycle failure",
                        },
                    )
                    self._active = None
                self._started = False
                self.lease.release()
        finally:
            self._operation.release()

    def __exit__(self, *args: object) -> None:
        self.close()
