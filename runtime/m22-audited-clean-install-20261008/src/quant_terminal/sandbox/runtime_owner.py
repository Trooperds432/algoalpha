"""Internal, one-use runtime ownership. Does not enable public admission."""

from __future__ import annotations

from quant_terminal.sandbox.podman import PodmanBackend, SandboxUnavailable
from quant_terminal.sandbox.wsl_keeper import WslKeeper


class IsolationRuntimeOwner:
    """Retain the OS lease, never a thread lock, between backend operations."""

    def __init__(self, backend: PodmanBackend, keeper: WslKeeper) -> None:
        if not backend._lock.acquire(blocking=False):
            raise SandboxUnavailable("isolation_busy")
        try:
            if backend._runtime_owner is not None or backend._ownership_depth:
                raise SandboxUnavailable("runtime_already_bound")
            if backend.config.wsl_distribution != keeper.distribution:
                raise ValueError("keeper distribution must match backend")
            self.backend = backend
            self.keeper = keeper
            self._state = "new"
            backend._runtime_owner = self
        finally:
            backend._lock.release()

    def start(self) -> None:
        with self.backend._ownership(recovery=True):
            if self._state != "new":
                raise SandboxUnavailable("runtime_already_used")
            self.backend._retained = True
            self._state = "starting"
            try:
                self.keeper.start()
                self.backend.reconcile()
                capability = self.backend.probe()
                if not capability.available:
                    raise SandboxUnavailable(capability.reason)
                self._guard()
                self._state = "ready"
            except BaseException:
                self._state = "unavailable"
                self.backend._invalidate()
                self.backend._mark_unhealthy()
                raise

    def _guard(self) -> None:
        if self._state not in ("starting", "ready"):
            raise SandboxUnavailable("runtime_unavailable")
        try:
            if self.backend._is_unhealthy():
                raise SandboxUnavailable("cleanup_unhealthy")
            self.keeper.ensure_available()
        except SandboxUnavailable:
            self._state = "unavailable"
            self.backend._invalidate()
            self.backend._mark_unhealthy()
            raise

    def _reserve(self, wall_seconds: float) -> None:
        self._guard()
        # Conservative bound: effective probe (<=152s), job create/inspect and
        # cleanup (<=76s), client reap/pipes (<=11s), watchdog (<=7s), margin.
        # No renewal; each CLI has 10s work + 3s reap + three 2s pipe joins.
        self.keeper.reserve(wall_seconds + 300)

    def ensure_available(self) -> None:
        """Readiness check for trusted admission, without launching an operation."""
        self._guard()

    def close(self) -> None:
        # A failed host-lock acquisition has not started any keeper or container.
        # Prove that locally rather than acquiring another owner's host lease.
        if not self.backend._lock.acquire(blocking=False):
            raise SandboxUnavailable("isolation_busy")
        try:
            if self._state == "closed":
                return
            if (
                self._state == "new"
                and not self.backend._retained
                and not self.backend._ownership_depth
            ):
                self._state = "closed"
                return
            self._close_started()
        finally:
            self.backend._lock.release()

    def _close_started(self) -> None:
        with self.backend._ownership(recovery=True):
            if self._state == "closed":
                return
            if not self.backend._retained:
                raise SandboxUnavailable("runtime_not_started")
            self._state = "unavailable"
            self.backend._invalidate()
            try:
                self.backend.reconcile()
                self.backend._audit("runtime_cleanup", outcome="ok")
                self.keeper.close(cleanup_confirmed=True)
                self.backend._audit("runtime_closed", outcome="ok")
            except BaseException:
                self.backend._mark_unhealthy()
                raise
            self._state = "closed"
            # Context exit releases only after inventory, leases, audit, keeper.
            self.backend._retained = False
