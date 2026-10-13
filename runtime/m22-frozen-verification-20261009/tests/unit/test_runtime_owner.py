"""Internal owner regressions; deterministic collaborators never launch containers."""

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_sandbox_podman import blob
from test_sandbox_podman import isolated_host_ownership as isolated_host_ownership
from test_wsl_keeper import local_transport as local_transport

from quant_terminal.sandbox.podman import (
    PodmanBackend,
    PodmanCapability,
    PodmanConfig,
    SandboxUnavailable,
)
from quant_terminal.sandbox.runtime_owner import IsolationRuntimeOwner
from quant_terminal.sandbox.wsl_keeper import WslKeeper


def test_never_started_owner_closes_without_acquiring_other_owners_lease(tmp_path, monkeypatch):
    from quant_terminal.application.isolated_jobs import IsolatedJobScheduler
    from quant_terminal.application.jobs import SchedulerLease

    backend = PodmanBackend(
        PodmanConfig("sha256:" + "a" * 64, "/profile", audit_path=tmp_path / "audit")
    )
    keeper = ControlledKeeper()
    owner = IsolationRuntimeOwner(backend, keeper)
    attempts = []

    def busy():
        attempts.append("acquire")
        raise SandboxUnavailable("isolation_busy")

    monkeypatch.setattr(backend._host_ownership, "acquire", busy)
    monkeypatch.setattr(keeper, "start", lambda: pytest.fail("keeper must not start"))
    monkeypatch.setattr(backend, "reconcile", lambda: pytest.fail("cannot reconcile other owner"))
    scheduler = IsolatedJobScheduler(tmp_path / "state", Path(__file__).parents[2], lambda: owner)
    with pytest.raises(SandboxUnavailable, match="isolation_busy"):
        scheduler.__enter__()
    assert attempts == ["acquire"]
    assert scheduler.lease.handle is None
    scheduler.close()
    owner.close()
    contender = SchedulerLease(scheduler.lease.path)
    contender.acquire()
    contender.release()
    assert not keeper.stopped
    with pytest.raises(SandboxUnavailable, match="runtime_already_used"):
        # This independent startup attempt still encounters contention first.
        monkeypatch.setattr(backend._host_ownership, "acquire", lambda: None)
        monkeypatch.setattr(backend._host_ownership, "release", lambda: None)
        owner.start()


def test_owner_startup_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    backend = PodmanBackend(
        PodmanConfig("sha256:" + "a" * 64, "/profile", audit_path=tmp_path / "audit")
    )

    class Keeper:
        distribution = backend.config.wsl_distribution

        def start(self) -> None:
            events.append("keeper")

        def ensure_available(self) -> None:
            pass

        def reserve(self, seconds: float) -> None:
            pass

        def close(self, *, cleanup_confirmed: bool) -> None:
            events.append("close")

    monkeypatch.setattr(backend._host_ownership, "acquire", lambda: events.append("lock"))
    monkeypatch.setattr(backend._host_ownership, "release", lambda: events.append("release"))
    monkeypatch.setattr(backend, "reconcile", lambda: events.append("reconcile") or 0)
    monkeypatch.setattr(
        backend, "_probe", lambda: events.append("probe") or PodmanCapability(True, "ok")
    )
    owner = IsolationRuntimeOwner(backend, Keeper())
    owner.start()
    assert events == ["lock", "keeper", "reconcile", "probe"]
    owner.close()
    assert events[-3:] == ["reconcile", "close", "release"]


class ControlledKeeper(WslKeeper):
    def __init__(self) -> None:
        super().__init__(lifetime_seconds=900)
        self.lost = False
        self.remaining = 900.0
        self.stopped = False

    def start(self) -> None:
        pass

    def ensure_available(self) -> None:
        if self.lost:
            raise SandboxUnavailable("keeper_transport_lost")

    def reserve(self, seconds: float) -> None:
        self.ensure_available()
        if seconds >= self.remaining:
            raise SandboxUnavailable("keeper_lifetime_insufficient")

    def close(self, *, cleanup_confirmed: bool) -> None:
        assert cleanup_confirmed
        self.stopped = True


def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backend = PodmanBackend(
        PodmanConfig("sha256:" + "a" * 64, "/profile", audit_path=tmp_path / "audit")
    )
    keeper = ControlledKeeper()
    monkeypatch.setattr(backend, "_recovery_run", lambda *args: b"")

    def probe():
        backend._verified = True
        backend._version = "test"
        backend._image_provenance = {"test": True}
        return PodmanCapability(True, "ok")

    monkeypatch.setattr(backend, "_probe", probe)
    owner = IsolationRuntimeOwner(backend, keeper)
    owner.start()
    return owner, backend, keeper


def test_retained_between_calls_and_sequential_cross_thread(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    other = PodmanBackend(backend.config)
    try:
        assert other.probe().reason == "isolation_busy"
        results = []
        for _ in range(2):
            thread = threading.Thread(target=lambda: results.append(backend.identity()))
            thread.start()
            thread.join(2)
            assert not thread.is_alive()
        assert len(results) == 2
        with backend._ownership():
            thread = threading.Thread(target=lambda: results.append(backend.probe().reason))
            thread.start()
            thread.join(2)
            assert not thread.is_alive()
        assert results[-1] == "isolation_busy"
        assert other.probe().reason == "isolation_busy"
    finally:
        owner.close()
    assert keeper.stopped
    with other._ownership():
        pass


def test_other_process_blocked_between_operations(tmp_path, monkeypatch, isolated_host_ownership):
    owner, backend, _ = prepared(tmp_path, monkeypatch)
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from quant_terminal.sandbox import podman as p
p._ownership_path=lambda:Path(sys.argv[2])
b=p.PodmanBackend(p.PodmanConfig('sha256:'+'a'*64,'/profile'))
b._probe=lambda:p.PodmanCapability(True,'test')
print(b.probe().reason)
"""

    def attempt():
        return subprocess.run(
            [
                getattr(sys, "_base_executable", sys.executable),
                "-I",
                "-c",
                script,
                str(Path(__file__).parents[2] / "src"),
                str(isolated_host_ownership),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()

    try:
        backend.identity()
        assert attempt() == "isolation_busy"
    finally:
        owner.close()
    assert attempt() == "test"


@pytest.mark.parametrize("stage", ["keeper", "reconcile", "probe"])
def test_startup_failure_retains_until_cleanup(tmp_path, monkeypatch, stage):
    backend = PodmanBackend(
        PodmanConfig("sha256:" + "a" * 64, "/profile", audit_path=tmp_path / "audit")
    )
    keeper = ControlledKeeper()
    calls = []

    def fail():
        calls.append(stage)
        raise SandboxUnavailable("startup_failed")

    monkeypatch.setattr(backend, "_recovery_run", lambda *args: b"")
    monkeypatch.setattr(backend, "_probe", lambda: PodmanCapability(True, "ok"))
    if stage == "keeper":
        monkeypatch.setattr(keeper, "start", fail)
    elif stage == "reconcile":
        monkeypatch.setattr(backend, "reconcile", fail)
    else:
        monkeypatch.setattr(backend, "_probe", fail)
    owner = IsolationRuntimeOwner(backend, keeper)
    try:
        with pytest.raises(SandboxUnavailable):
            owner.start()
        assert PodmanBackend(backend.config).probe().reason == "isolation_busy"
        assert backend._is_unhealthy()
        with pytest.raises(SandboxUnavailable, match="runtime_unavailable"):
            backend.identity()
    finally:
        monkeypatch.setattr(backend, "reconcile", lambda: 0)
        owner.close()


def test_cached_identity_fails_after_loss(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    assert backend.identity()
    keeper.lost = True
    try:
        with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
            backend.identity()
        assert not backend._verified
        assert backend._version is None
        assert backend._image_provenance == {}
        assert PodmanBackend(backend.config).probe().reason == "isolation_busy"
    finally:
        owner.close()


def test_lifetime_rejected_before_probe_or_create(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    keeper.remaining = 305
    monkeypatch.setattr(backend, "_probe", lambda: pytest.fail("no probe with short lifetime"))
    monkeypatch.setattr(backend, "_create", lambda *args: pytest.fail("no create"))
    try:
        with pytest.raises(SandboxUnavailable, match="keeper_lifetime_insufficient"):
            backend.execute(blob())
    finally:
        owner.close()


def test_loss_before_launch_rejects(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    keeper.lost = True
    monkeypatch.setattr(backend, "_create", lambda *args: pytest.fail("no create after loss"))
    try:
        with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
            backend.execute(blob())
    finally:
        owner.close()


def test_loss_during_local_stream_stops_client_and_allows_recovery(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    timer = threading.Timer(0.15, lambda: setattr(keeper, "lost", True))
    timer.start()
    try:
        with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
            backend._stream(
                (sys.executable, "-I", "-c", "import time;time.sleep(10)"), b"", 1024, 12, None
            )
        # Real bounded local process through the private recovery stream.
        assert (
            backend._stream(
                (sys.executable, "-I", "-c", "print('recovered')"),
                b"",
                1024,
                2,
                None,
                _recovery=True,
            )[0].strip()
            == b"recovered"
        )
    finally:
        timer.join(2)
        owner.close()


@pytest.mark.parametrize("failure", ["remove", "inventory", "audit", "lease"])
def test_failed_close_retains_ownership_and_unhealthy(tmp_path, monkeypatch, failure):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    lease = backend._begin_lease()
    original_audit = backend._audit
    original_unlink = Path.unlink

    def recovery(args, limit=64_000):
        if failure == "remove" and args[0] == "rm":
            raise SandboxUnavailable("remove_failed")
        return ("f" * 64).encode() if failure in ("remove", "inventory") else b""

    monkeypatch.setattr(backend, "_recovery_run", recovery)

    def audit(*args, **kwargs):
        if args[0] == "runtime_cleanup":
            raise SandboxUnavailable("audit_write_failed")
        return original_audit(*args, **kwargs)

    def unlink(path, *args, **kwargs):
        if path == lease:
            raise OSError("lease deletion failure")
        return original_unlink(path, *args, **kwargs)

    if failure == "audit":
        monkeypatch.setattr(backend, "_audit", audit)
    if failure == "lease":
        monkeypatch.setattr(Path, "unlink", unlink)
    try:
        with pytest.raises((SandboxUnavailable, OSError)):
            owner.close()
        assert backend._is_unhealthy()
        assert not keeper.stopped
        assert PodmanBackend(backend.config).probe().reason == "isolation_busy"
    finally:
        monkeypatch.setattr(backend, "_recovery_run", lambda *args: b"")
        monkeypatch.setattr(backend, "_audit", original_audit)
        monkeypatch.setattr(Path, "unlink", original_unlink)
        owner.close()


def test_nested_execute_probe_owned_depth(tmp_path, monkeypatch):
    owner, backend, _ = prepared(tmp_path, monkeypatch)
    observed = []

    def unavailable():
        observed.append(backend._ownership_depth)
        return PodmanCapability(False, "test_reject")

    monkeypatch.setattr(backend, "_probe", unavailable)
    try:
        with pytest.raises(SandboxUnavailable, match="test_reject"):
            backend.execute(blob())
        assert observed == [2]
        assert backend._ownership_depth == 0
        assert backend._retained
    finally:
        owner.close()


def test_owner_real_expired_keeper_teardown(tmp_path, monkeypatch, local_transport):
    backend = PodmanBackend(
        PodmanConfig("sha256:" + "a" * 64, "/profile", audit_path=tmp_path / "audit")
    )
    keeper = WslKeeper(lifetime_seconds=1)
    monkeypatch.setattr(backend, "_recovery_run", lambda *args: b"")
    monkeypatch.setattr(backend, "_probe", lambda: PodmanCapability(True, "ok"))
    owner = IsolationRuntimeOwner(backend, keeper)
    owner.start()
    time.sleep(1.5)
    assert not backend.probe().available
    owner.close()
    with pytest.raises(SandboxUnavailable):
        keeper.ensure_available()
    with PodmanBackend(backend.config)._ownership():
        pass


def test_create_failure_uses_recovery_after_keeper_loss(tmp_path, monkeypatch):
    from test_sandbox_podman import BUDGET

    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    calls = []

    def fail_create(args):
        keeper.lost = True
        backend._guard()
        pytest.fail("guard should reject")

    def recover(args, limit=64_000):
        calls.append(args)
        return b"f" * 64 if any("algoalpha.launch=" in arg for arg in args) else b""

    monkeypatch.setattr(backend, "_run", fail_create)
    monkeypatch.setattr(backend, "_recovery_run", recover)
    try:
        with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
            backend._create(BUDGET, tmp_path / "test.cid", "test")
        assert [args[0] for args in calls] == ["ps", "rm", "ps"]
        assert backend._is_unhealthy()
        assert backend._retained
    finally:
        owner.close()


def test_cleanup_private_path_keeps_real_stream_limits_and_lock(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    original_stream = backend._stream
    calls = []
    monkeypatch.setattr(backend, "_recovery_run", PodmanBackend._recovery_run.__get__(backend))
    monkeypatch.setattr(backend, "_executable", lambda: sys.executable)

    def local_stream(args, data, limit, wall, cancel, watcher=None, *, _recovery=False):
        calls.append((args[1], limit, wall, _recovery))
        return original_stream(
            (sys.executable, "-I", "-c", "pass"),
            data,
            limit,
            wall,
            cancel,
            watcher,
            _recovery=_recovery,
        )

    monkeypatch.setattr(backend, "_stream", local_stream)
    keeper.lost = True
    try:
        with pytest.raises(SandboxUnavailable):
            backend.identity()
        backend._cleanup("f" * 64)
        assert calls == [("rm", 64_000, 10, True), ("ps", 64_000, 10, True)]
        assert PodmanBackend(backend.config).probe().reason == "isolation_busy"
    finally:
        owner.close()


def test_execute_loss_cleans_before_lease_deletion(tmp_path, monkeypatch):
    import json

    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(backend, "_create", lambda *args: "f" * 64)
    monkeypatch.setattr(backend, "_inspect", lambda *args: None)
    monkeypatch.setattr(backend, "_executable", lambda: sys.executable)
    monkeypatch.setattr(backend, "_watchdog", lambda *args: object())
    monkeypatch.setattr(backend, "_stop_watchdog", lambda *args: calls.append("watchdog_stopped"))

    def stream(*args):
        keeper.lost = True
        backend._guard()

    def recovery(args):
        calls.append(args[0])
        return b""

    monkeypatch.setattr(backend, "_stream", stream)
    monkeypatch.setattr(backend, "_recovery_run", recovery)
    try:
        with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
            backend.execute(blob())
        assert calls == ["rm", "ps", "watchdog_stopped"]
        assert backend._leases() == ()
        events = [
            json.loads(line)["event"] for line in backend.config.audit_path.read_text().splitlines()
        ]
        assert events[-2:] == ["cleanup", "termination"]
        assert backend._retained
    finally:
        owner.close()


def test_stream_checks_loss_after_fast_exit(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)

    def completed(*args):
        keeper.lost = True
        return b"ok", 0

    monkeypatch.setattr(backend, "_stream_owned", completed)
    try:
        with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
            backend._stream(("unused",), b"", 20, 1, None)
    finally:
        owner.close()


def test_stale_lease_reconciled_before_probe_without_source(tmp_path, monkeypatch):
    backend = PodmanBackend(
        PodmanConfig("sha256:" + "a" * 64, "/profile", audit_path=tmp_path / "audit")
    )
    lease = tmp_path / "audit.stale.pending"
    lease.write_text('{"scheduler_pid": 0}')
    calls = []

    def recovery(args):
        calls.append(args[0])
        return b""

    monkeypatch.setattr(backend, "_recovery_run", recovery)

    def probe():
        assert not lease.exists()
        assert calls == ["ps", "ps"]
        return PodmanCapability(True, "ok")

    monkeypatch.setattr(backend, "_probe", probe)
    monkeypatch.setattr(backend, "_create", lambda *args: pytest.fail("must not rerun source"))
    owner = IsolationRuntimeOwner(backend, ControlledKeeper())
    owner.start()
    owner.close()


def test_parent_death_releases_retained_owner_and_successor_reconciles(
    tmp_path, monkeypatch, isolated_host_ownership
):
    script = """
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from quant_terminal.sandbox import podman as p
from quant_terminal.sandbox.runtime_owner import IsolationRuntimeOwner
from quant_terminal.sandbox.wsl_keeper import WslKeeper
p._ownership_path=lambda:Path(sys.argv[2])
b=p.PodmanBackend(p.PodmanConfig('sha256:'+'a'*64,'/profile',audit_path=Path(sys.argv[3])))
b._recovery_run=lambda *args:b''
b._probe=lambda:p.PodmanCapability(True,'test')
k=WslKeeper()
k.start=lambda:None
k.ensure_available=lambda:None
o=IsolationRuntimeOwner(b,k)
o.start()
b._begin_lease()
print('READY',flush=True)
time.sleep(30)
"""
    audit = tmp_path / "audit"
    child = subprocess.Popen(
        [
            getattr(sys, "_base_executable", sys.executable),
            "-I",
            "-u",
            "-c",
            script,
            str(Path(__file__).parents[2] / "src"),
            str(isolated_host_ownership),
            str(audit),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = []

    def read():
        ready.append(child.stdout.readline())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        reader.join(5)
        assert ready == ["READY\n"]
        backend = PodmanBackend(PodmanConfig("sha256:" + "a" * 64, "/profile", audit_path=audit))
        assert backend.probe().reason == "isolation_busy"
        child.kill()
        child.wait(3)
        assert backend._leases()
        monkeypatch.setattr(backend, "_recovery_run", lambda *args: b"")

        def probe():
            assert backend._leases() == ()
            return PodmanCapability(True, "test")

        monkeypatch.setattr(backend, "_probe", probe)
        monkeypatch.setattr(backend, "_create", lambda *args: pytest.fail("never replay source"))
        successor = IsolationRuntimeOwner(backend, ControlledKeeper())
        successor.start()
        successor.close()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(3)
        reader.join(2)
        child.stdout.close()
        child.stderr.close()


def test_unverified_guardian_cleanup_retains_runtime_owner(tmp_path, monkeypatch):
    owner, backend, original_keeper = prepared(tmp_path, monkeypatch)
    keeper = WslKeeper(lifetime_seconds=1)
    keeper._process = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-c", "raise SystemExit(1)"]
    )
    keeper._process.wait(timeout=3)
    owner.keeper = keeper
    try:
        with pytest.raises(SandboxUnavailable, match="keeper_cleanup_unverified"):
            owner.close()
        assert backend._retained and backend._is_unhealthy()
        assert PodmanBackend(backend.config).probe().reason == "isolation_busy"
    finally:
        owner.keeper = original_keeper
        owner.close()


def test_final_runtime_audit_failure_retains_owner_after_keeper_stop(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    original_audit = backend._audit

    def audit(event, *args, **kwargs):
        if event == "runtime_closed":
            raise SandboxUnavailable("audit_write_failed")
        return original_audit(event, *args, **kwargs)

    monkeypatch.setattr(backend, "_audit", audit)
    try:
        with pytest.raises(SandboxUnavailable, match="audit_write_failed"):
            owner.close()
        assert keeper.stopped
        assert backend._retained and backend._is_unhealthy()
        assert PodmanBackend(backend.config).probe().reason == "isolation_busy"
    finally:
        monkeypatch.setattr(backend, "_audit", original_audit)
        owner.close()


def test_health_marker_failure_still_invalidates_and_retains_owner(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    original_open = Path.open

    def open_path(path, *args, **kwargs):
        if path.suffix == ".unhealthy":
            raise OSError("marker unavailable")
        return original_open(path, *args, **kwargs)

    keeper.lost = True
    monkeypatch.setattr(Path, "open", open_path)
    try:
        with pytest.raises(SandboxUnavailable, match="health_write_failed"):
            backend.identity()
        assert not backend._verified and backend._retained
        assert backend._is_unhealthy()
        assert PodmanBackend(backend.config).probe().reason == "isolation_busy"
    finally:
        monkeypatch.setattr(Path, "open", original_open)
        owner.close()


def test_closed_owner_rejects_operations_and_cannot_restart(tmp_path, monkeypatch):
    owner, backend, keeper = prepared(tmp_path, monkeypatch)
    owner.close()
    owner.close()
    assert backend.probe().reason == "runtime_unavailable"
    for action in (backend.identity, lambda: backend.execute(blob())):
        with pytest.raises(SandboxUnavailable, match="runtime_unavailable"):
            action()
    with pytest.raises(SandboxUnavailable, match="runtime_already_used"):
        owner.start()
    assert not backend._retained
