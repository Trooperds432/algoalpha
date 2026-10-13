"""Trusted policy, bounded I/O and fail-closed lifecycle tests (no hostile host source)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from quant_terminal.sandbox.podman import (
    PodmanBackend,
    PodmanCapability,
    PodmanConfig,
    SandboxUnavailable,
    cleanup_command,
    command_for,
    read_container_id,
)
from quant_terminal.sandbox.protocol import SandboxBudget

IMAGE = "sha256:" + "a" * 64
BUDGET = SandboxBudget(10, 5, 64_000_000, 4, 32, 0, 0, 2048)


@pytest.fixture(autouse=True)
def isolated_host_ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Exercise real OS locking without writing the user's runtime directory."""
    from quant_terminal.sandbox import podman

    # Windows temp directories can inherit broad write ACLs. Use an explicitly
    # private test-only namespace; production must reject that unsafe inheritance.
    parent = tmp_path / "ownership"
    parent.mkdir(mode=0o700)
    if sys.platform == "win32":
        user = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        sid = next(csv.reader(io.StringIO(user.stdout.strip())))[1]
        subprocess.run(
            [
                "icacls",
                str(parent),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            capture_output=True,
            check=True,
            timeout=5,
        )
    path = parent / "sandbox" / "host-ownership.lock"
    monkeypatch.setattr(podman, "_ownership_path", lambda: path)
    return path


@contextmanager
def holding_probe_process(lock_path: Path) -> Iterator[subprocess.Popen[str]]:
    # Trusted test helper only: no container/source execution. A real probe holds
    # ownership while its effective-control collaborator is deliberately paused.
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from quant_terminal.sandbox import podman
podman._ownership_path = lambda: Path(sys.argv[1])
backend = podman.PodmanBackend(podman.PodmanConfig('sha256:'+'a'*64, '/profile'))
def paused_probe():
    print('READY', flush=True)
    sys.stdin.readline()
    return podman.PodmanCapability(True, 'controls_verified')
backend._probe = paused_probe
assert backend.probe().available
"""
    process = subprocess.Popen(
        [
            # Bypass Windows venv redirectors: killing their wrapper is not proof
            # that the actual Python process holding ownership has died.
            getattr(sys, "_base_executable", sys.executable),
            "-I",
            "-c",
            script,
            str(lock_path),
            str(Path(__file__).parents[2] / "src"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = threading.Event()
    lines: list[str] = []

    def read_ready() -> None:
        assert process.stdout is not None
        lines.append(process.stdout.readline())
        ready.set()

    reader = threading.Thread(target=read_ready, daemon=True)
    reader.start()
    try:
        assert ready.wait(5), "trusted probe helper did not become ready"
        assert lines == ["READY\n"] and process.poll() is None
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        reader.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def config(tmp_path: Path) -> PodmanConfig:
    return PodmanConfig(
        IMAGE, "/home/user/algoalpha-seccomp.json", audit_path=tmp_path / "audit.jsonl"
    )


def blob(dependencies: dict[str, str] | None = None) -> bytes:
    source = "def on_bar(context, bar): return []"
    return json.dumps(
        {
            "version": 1,
            "job_id": "job-1",
            "source": source,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "data_rows": [{"close": "100"}],
            "data_sha256": hashlib.sha256(b'[{"close":"100"}]').hexdigest(),
            "dependencies": dependencies or {},
            "seed": 1,
            "budget": {key: getattr(BUDGET, key) for key in BUDGET.__dataclass_fields__},
        }
    ).encode()


def successful_control_probe(backend: PodmanBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetic effective-control evidence; never starts Podman or source code."""
    info = {
        "host": {
            "security": {"rootless": True, "seccompEnabled": True},
            "cgroupVersion": "v2",
            "cgroupControllers": ["cpu", "memory", "pids"],
        },
        "version": {"Version": "5.8.8"},
        "Client": {"Version": "5.8.3"},
    }
    image = {"Id": IMAGE[7:], "Config": {"Env": [], "Volumes": None}}
    controls = {
        "uid": 65532,
        "seccomp": "2",
        "nnp": "1",
        "caps": "0000000000000000",
        "memory": "64000000",
        "swap": "0",
        "pids": "4",
        "net": ["lo"],
        "sockets_denied": True,
        "cpu": "50000 100000",
        "python_version": "3.13.15",
        "image_files": {"strategy_worker.py": "c" * 64},
    }
    monkeypatch.setattr(
        backend, "_run", lambda args: json.dumps(info if args[0] == "info" else [image])
    )
    monkeypatch.setattr(backend, "_create", lambda *args: "f" * 64)
    monkeypatch.setattr(backend, "_inspect", lambda *args: None)
    monkeypatch.setattr(backend, "_stream", lambda *args: (json.dumps(controls).encode(), 0))
    monkeypatch.setattr(backend, "_cleanup", lambda *args: None)
    monkeypatch.setattr(backend, "_profile_digest", lambda: "b" * 64)


def test_audit_preserves_verified_identity_after_readiness_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    successful_control_probe(backend, monkeypatch)
    assert backend.probe().available
    verified = backend._payload("launch")
    backend._invalidate()
    event = backend._payload("termination", outcome="keeper_transport_lost")
    assert event["version"] == verified["version"] == "5.8.8"
    assert event["image_provenance"] == verified["image_provenance"]
    assert verified["identity_status"] == "currently_verified"
    assert event["identity_status"] == "last_verified"
    # A consumer cannot corrupt either the immutable audit snapshot or the
    # currently enforced execution identity through a returned event object.
    event["image_provenance"]["image_files"]["strategy_worker.py"] = "tampered"
    assert backend._payload("cleanup")["image_provenance"]["image_files"] == {
        "strategy_worker.py": "c" * 64
    }
    assert not backend._verified and backend._version is None and not backend._image_provenance
    monkeypatch.setattr(backend, "probe", lambda: PodmanCapability(False, "keeper_transport_lost"))
    with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
        backend.identity()
    monkeypatch.setattr(
        backend, "_begin_lease", lambda: pytest.fail("audit history cannot admit source")
    )
    with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
        backend.execute(blob())


def test_unverified_or_failed_probe_never_creates_audit_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    initial = backend._payload("admission")
    assert initial["version"] is None and initial["image_provenance"] == {}
    assert initial["identity_status"] == "unverified"
    successful_control_probe(backend, monkeypatch)
    monkeypatch.setattr(
        backend,
        "_cleanup",
        lambda *args: (_ for _ in ()).throw(SandboxUnavailable("cleanup_failed")),
    )
    assert not backend.probe().available
    failed = backend._payload("rejection")
    assert failed["identity_status"] == "unverified"
    assert failed["version"] is None and failed["image_provenance"] == {}


def test_failed_reprobe_preserves_only_the_previous_verified_audit_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    successful_control_probe(backend, monkeypatch)
    assert backend.probe().available
    expected = backend._payload("launch")["image_provenance"]
    backend._invalidate()
    # A partially inspected replacement can change volatile fields, but cannot
    # become the recorded verified boundary without effective-control success.
    previous_run = backend._run

    def changed_version(args):
        result = json.loads(previous_run(args))
        if args[0] == "info":
            result["version"]["Version"] = "9.0.0"
        return json.dumps(result)

    monkeypatch.setattr(backend, "_run", changed_version)
    monkeypatch.setattr(backend, "_stream", lambda *args: (b"", 71))
    assert not backend.probe().available
    event = backend._payload("termination", outcome="effective_probe_failed")
    assert event["identity_status"] == "last_verified"
    assert event["version"] == "5.8.8"
    assert event["image_provenance"] == expected
    assert backend._version == "9.0.0" and not backend._verified


def test_rejects_mutable_image_and_windows_profile(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="digest-pinned"):
        PodmanConfig("python:3.13", "/profile.json")
    with pytest.raises(ValueError, match="absolute Linux"):
        PodmanConfig(IMAGE, "C:\\Users\\profile.json")
    with pytest.raises(ValueError, match="absolute Linux"):
        PodmanConfig(IMAGE, "/vm/../profile.json")
    with pytest.raises(ValueError, match="absolute"):
        PodmanConfig(IMAGE, "/profile", audit_path=Path("relative"))
    with pytest.raises(ValueError, match="distribution"):
        PodmanConfig(IMAGE, "/profile", wsl_distribution="--help")


def test_command_enforces_confinement_and_no_host_mounts(tmp_path: Path) -> None:
    command = command_for(config(tmp_path), BUDGET, tmp_path / "job.cid", "job-1")
    for flag in (
        "--network=none",
        "--read-only",
        "--read-only-tmpfs=false",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65532:65532",
        "--userns=auto",
        "--ipc=none",
        "--pid=private",
        "--uts=private",
        "--cgroupns=private",
        "--pids-limit=4",
        "--memory=64000000",
        "--memory-swap=64000000",
        "--cpus=0.5000",
        "--ulimit=cpu=5:5",
        "--ulimit=nofile=32:32",
        "--ulimit=fsize=2048:2048",
        "--timeout=10",
        "--log-driver=none",
        "--http-proxy=false",
        "--restart=no",
        "--pull=never",
    ):
        assert flag in command
    assert all(
        not x.startswith(("--volume", "--publish", "--privileged", "--device=")) for x in command
    )
    assert not any(x.startswith("--tmpfs") for x in command)
    assert command[-4:] == (IMAGE, "-I", "-S", "/opt/algoalpha/strategy_worker.py")


def test_unenforceable_limits_and_ids_reject(tmp_path: Path) -> None:
    for budget in (
        replace(BUDGET, scratch_bytes=1),
        replace(BUDGET, scratch_files=1),
        replace(BUDGET, cpu_seconds=11),
        replace(BUDGET, wall_seconds=3600),
    ):
        with pytest.raises(SandboxUnavailable):
            command_for(config(tmp_path), budget, tmp_path / "cid", "job")
    with pytest.raises(ValueError, match="job ID"):
        command_for(config(tmp_path), BUDGET, tmp_path / "cid", "../secret")
    with pytest.raises(ValueError, match="absolute"):
        command_for(config(tmp_path), BUDGET, Path("relative"), "job")
    rounded = command_for(
        config(tmp_path), replace(BUDGET, wall_seconds=7, cpu_seconds=3), tmp_path / "cid", "job"
    )
    assert "--cpus=0.4285" in rounded  # never round a total CPU budget upwards


def test_container_id_and_label_guarded_cleanup(tmp_path: Path) -> None:
    cidfile = tmp_path / "cid"
    cidfile.write_text("../other")
    assert read_container_id(cidfile) is None
    cidfile.write_text("f" * 64)
    assert read_container_id(cidfile) == "f" * 64
    assert "--filter=label=algoalpha.owner=quant-terminal-m16" in cleanup_command("f" * 64)
    assert f"--filter=id=^{'f' * 64}$" in cleanup_command("f" * 64)
    with pytest.raises(ValueError):
        cleanup_command("--all")


def test_audit_required_before_any_engine_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(PodmanConfig(IMAGE, "/profile"))
    monkeypatch.setattr(backend, "probe", lambda: pytest.fail("no runtime without audit"))
    with pytest.raises(SandboxUnavailable, match="audit_unavailable"):
        backend.execute(blob())
    backend = PodmanBackend(replace(config(tmp_path), audit_path=tmp_path))
    with pytest.raises(SandboxUnavailable, match="audit_write_failed"):
        backend.execute(blob())


def test_dependencies_reject_before_source_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    monkeypatch.setattr(
        backend, "probe", lambda: pytest.fail("unsupported dependency before probe")
    )
    with pytest.raises(SandboxUnavailable, match="dependencies_unsupported"):
        backend.execute(blob({"package==1.0": "b" * 64}))
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["admission", "rejection"]
    assert all("source" not in event for event in events)
    assert events[0]["source_sha256"]


def test_cleanup_failure_stays_closed_across_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))

    def fail(*args: object, **kwargs: object) -> bytes:
        raise SandboxUnavailable("podman_command_failed")

    monkeypatch.setattr(backend, "_run", fail)
    with pytest.raises(SandboxUnavailable, match="container_cleanup_failed"):
        backend._cleanup("f" * 64)
    other = PodmanBackend(config(tmp_path))
    assert other.probe().reason == "cleanup_unhealthy"
    assert (tmp_path / "audit.unhealthy").exists()


def test_successful_reconciliation_clears_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    backend._mark_unhealthy()
    monkeypatch.setattr(backend, "_run", lambda *args, **kwargs: b"")
    assert backend.reconcile() == 0
    assert not backend._is_unhealthy()
    assert not (tmp_path / "audit.unhealthy").exists()


def test_bounded_output_and_cancellation_use_harmless_process(tmp_path: Path) -> None:
    backend = PodmanBackend(config(tmp_path))
    with pytest.raises(SandboxUnavailable, match="output_limit"):
        backend._stream((sys.executable, "-c", "print('x'*8192)"), b"", 1024, 2, None)
    event = threading.Event()
    event.set()
    with pytest.raises(SandboxUnavailable, match="cancelled"):
        backend._stream((sys.executable, "-c", "import time;time.sleep(0.5)"), b"", 1024, 2, event)


def test_unverified_identity_and_capability_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    monkeypatch.setattr(backend, "probe", lambda: PodmanCapability(False, "engine_unavailable"))
    with pytest.raises(SandboxUnavailable, match="engine_unavailable"):
        backend.identity()
    with pytest.raises(SandboxUnavailable, match="engine_unavailable"):
        backend.execute(blob())


def test_client_environment_excludes_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from quant_terminal.sandbox.podman import _client_environment

    monkeypatch.setenv("TRADING212_SECRET", "broker-secret-sentinel")
    monkeypatch.setenv("OPENROUTER_API_KEY", "model-secret-sentinel")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "agent-secret-sentinel")
    monkeypatch.setenv("USERPROFILE", "C:/Users/Example")
    env = _client_environment()
    assert env["USERPROFILE"] == "C:/Users/Example"
    assert not any("secret-sentinel" in value for value in env.values())


def test_preset_cancel_never_probes_or_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    event = threading.Event()
    event.set()
    monkeypatch.setattr(backend, "probe", lambda: pytest.fail("already cancelled"))
    with pytest.raises(SandboxUnavailable, match="cancelled"):
        backend.execute(blob(), cancel=event)
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [entry["event"] for entry in events] == ["admission", "rejection"]
    assert events[-1]["outcome"] == "cancelled"


def test_expected_identity_rejects_after_probe_before_source_container_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    monkeypatch.setattr(backend, "probe", lambda: PodmanCapability(True, "controls_verified"))
    monkeypatch.setattr(backend, "_identity", lambda: "fresh-probe-changed-backend")
    monkeypatch.setattr(backend, "_begin_lease", lambda: pytest.fail("source lease created"))
    monkeypatch.setattr(backend, "_create", lambda *args: pytest.fail("source container created"))
    with pytest.raises(SandboxUnavailable, match="backend_identity_mismatch"):
        backend.execute(blob(), expected_identity="prepared-backend")
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["admission", "rejection"]
    assert events[-1]["reason"] == "backend_identity_mismatch"


def test_watchdog_acquires_current_parent_identity_then_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quant_terminal.sandbox.protocol import decode_request

    backend = PodmanBackend(config(tmp_path))
    monkeypatch.setattr(backend, "_executable", lambda: sys.executable)
    lease = backend._begin_lease()
    watcher = backend._watchdog("f" * 64, decode_request(blob()), lease)
    assert watcher.poll() is None
    backend._stop_watchdog(watcher)
    assert watcher.poll() is not None
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert events[0]["event"] == "watchdog_ready"
    assert events[0]["reason"] == "retained_parent_handle"
    lease.unlink()


def test_health_write_failure_preserves_durable_pending_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quant_terminal.sandbox.podman as module

    monkeypatch.setattr(module, "_ACTIVE_LEASES", set())
    monkeypatch.setattr(module, "_PROCESS_UNHEALTHY", set())
    backend = PodmanBackend(config(tmp_path))
    lease = backend._begin_lease()
    original = Path.open

    def deny_health(path: Path, *args: object, **kwargs: object):
        if path.suffix == ".unhealthy":
            raise OSError("storage unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_health)
    with pytest.raises(SandboxUnavailable, match="health_write_failed"):
        backend._mark_unhealthy()
    assert PodmanBackend(config(tmp_path)).probe().reason == "cleanup_unhealthy"
    # Simulate a fresh scheduler process which has no registered live leases.
    module._ACTIVE_LEASES.clear()
    module._PROCESS_UNHEALTHY.clear()
    assert lease.exists()
    assert PodmanBackend(config(tmp_path)).probe().reason == "cleanup_unhealthy"


def test_health_preflight_failure_prevents_container_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    monkeypatch.setattr(backend, "probe", lambda: PodmanCapability(True, "controls_verified"))
    monkeypatch.setattr(
        backend, "_create", lambda *args: pytest.fail("no create without durable health")
    )
    original = Path.open

    def deny_pending(path: Path, *args: object, **kwargs: object):
        if path.suffix == ".pending":
            raise OSError("storage unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_pending)
    with pytest.raises(SandboxUnavailable, match="health_preflight_failed"):
        backend.execute(blob())


def test_premature_watchdog_exit_fails_closed(tmp_path: Path) -> None:
    import subprocess

    backend = PodmanBackend(config(tmp_path))
    watcher = subprocess.Popen((sys.executable, "-c", "pass"))
    watcher.wait(timeout=2)
    with pytest.raises(SandboxUnavailable, match="watchdog_exit"):
        backend._stop_watchdog(watcher)
    assert PodmanBackend(config(tmp_path)).probe().reason == "cleanup_unhealthy"


@pytest.mark.parametrize("operation", ["probe", "execute", "reconcile"])
def test_other_backend_rejects_before_any_podman_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    owner = PodmanBackend(config(tmp_path))
    other = PodmanBackend(
        replace(config(tmp_path), audit_path=tmp_path / "independent-audit.jsonl")
    )
    monkeypatch.setattr(other, "_probe", lambda: pytest.fail("occupied host must not probe"))
    monkeypatch.setattr(other, "_run", lambda *args: pytest.fail("occupied host must not run"))
    with owner._ownership():
        if operation == "probe":
            assert other.probe() == PodmanCapability(False, "isolation_busy")
        else:
            with pytest.raises(SandboxUnavailable, match="isolation_busy"):
                if operation == "execute":
                    other.execute(blob())
                else:
                    other.reconcile()
    assert not (tmp_path / "independent-audit.jsonl").exists()


def test_execute_nested_effective_probe_is_reentrant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    observed: list[int] = []

    def unavailable() -> PodmanCapability:
        observed.append(backend._ownership_depth)
        return PodmanCapability(False, "engine_unavailable")

    monkeypatch.setattr(backend, "_probe", unavailable)
    with pytest.raises(SandboxUnavailable, match="engine_unavailable"):
        backend.execute(blob())
    assert observed == [2]
    assert backend._ownership_depth == 0
    # Both nested levels have released; another backend can subsequently own it.
    with PodmanBackend(config(tmp_path))._ownership():
        pass


def test_same_backend_competing_thread_rejects_promptly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PodmanBackend(config(tmp_path))
    monkeypatch.setattr(backend, "_probe", lambda: pytest.fail("competing thread must not probe"))
    result: list[PodmanCapability] = []
    with backend._ownership():
        thread = threading.Thread(target=lambda: result.append(backend.probe()))
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert result == [PodmanCapability(False, "isolation_busy")]


def test_real_process_probe_excludes_reconciliation_then_crash_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_host_ownership: Path
) -> None:
    backend = PodmanBackend(config(tmp_path))
    commands: list[tuple[str, ...]] = []

    def run(args: tuple[str, ...]) -> bytes:
        commands.append(args)
        return b""

    monkeypatch.setattr(backend, "_run", run)
    with holding_probe_process(isolated_host_ownership) as process:
        with pytest.raises(SandboxUnavailable, match="isolation_busy"):
            backend.reconcile()
        assert commands == []
        process.kill()
        process.wait(timeout=5)
        # Crash-released ownership: no lock-file deletion or takeover needed.
        assert backend.reconcile() == 0
        assert len(commands) == 1
    assert isolated_host_ownership.exists()


def test_real_process_probe_excludes_execution_and_another_effective_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_host_ownership: Path
) -> None:
    backend = PodmanBackend(config(tmp_path))
    probes: list[str] = []

    def probe() -> PodmanCapability:
        probes.append("effective")
        return PodmanCapability(False, "engine_unavailable")

    monkeypatch.setattr(backend, "_probe", probe)
    with holding_probe_process(isolated_host_ownership):
        assert backend.probe().reason == "isolation_busy"
        with pytest.raises(SandboxUnavailable, match="isolation_busy"):
            backend.execute(blob())
        assert probes == []
    assert backend.probe().reason == "engine_unavailable"
    assert probes == ["effective"]


def test_ownership_setup_failure_blocks_all_effective_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quant_terminal.sandbox import podman

    occupied = tmp_path / "not-a-directory"
    occupied.write_bytes(b"existing trusted file")
    monkeypatch.setattr(podman, "_ownership_path", lambda: occupied / "owner.lock")
    backend = PodmanBackend(config(tmp_path))
    monkeypatch.setattr(backend, "_probe", lambda: pytest.fail("no probe without ownership"))
    monkeypatch.setattr(backend, "_run", lambda *args: pytest.fail("no engine without ownership"))
    assert backend.probe().reason == "ownership_lock_unavailable"
    for action in (lambda: backend.execute(blob()), backend.reconcile):
        with pytest.raises(SandboxUnavailable, match="ownership_lock_unavailable"):
            action()
    assert not (tmp_path / "audit.jsonl").exists()


def test_independent_audits_share_ownership_not_health_or_pending_state(tmp_path: Path) -> None:
    first = PodmanBackend(config(tmp_path))
    second = PodmanBackend(replace(config(tmp_path), audit_path=tmp_path / "second.jsonl"))
    lease = first._begin_lease()
    try:
        first._mark_unhealthy()
        assert first._is_unhealthy()
        assert not second._is_unhealthy()
        assert second._leases() == ()
        with first._ownership():
            assert second.probe().reason == "isolation_busy"
    finally:
        lease.unlink()


def test_lock_leaf_symlink_is_rejected_without_touching_target(
    tmp_path: Path, isolated_host_ownership: Path
) -> None:
    isolated_host_ownership.parent.mkdir(mode=0o700)
    target = tmp_path / "untouched.lock"
    target.write_bytes(b"unchanged sentinel")
    try:
        isolated_host_ownership.symlink_to(target)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows host does not grant symlink creation privilege")
        raise
    backend = PodmanBackend(config(tmp_path))
    assert backend.probe().reason == "ownership_lock_unavailable"
    assert target.read_bytes() == b"unchanged sentinel"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction control")
@pytest.mark.parametrize("leaf", [False, True])
def test_native_windows_reparse_directory_or_leaf_is_rejected(
    tmp_path: Path, isolated_host_ownership: Path, leaf: bool
) -> None:
    import _winapi

    target = tmp_path / "junction-target"
    target.mkdir()
    if leaf:
        isolated_host_ownership.parent.mkdir()
        junction = isolated_host_ownership
    else:
        junction = isolated_host_ownership.parent
    _winapi.CreateJunction(str(target), str(junction))
    try:
        backend = PodmanBackend(config(tmp_path))
        assert backend.probe().reason == "ownership_lock_unavailable"
        assert list(target.iterdir()) == []
    finally:
        # Remove this test junction itself, never recurse through its target.
        os.rmdir(junction)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACL control")
@pytest.mark.parametrize("leaf", [False, True])
def test_native_windows_unsafe_directory_or_leaf_acl_blocks_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_host_ownership: Path, leaf: bool
) -> None:
    isolated_host_ownership.parent.mkdir()
    target = isolated_host_ownership if leaf else isolated_host_ownership.parent
    if leaf:
        target.write_bytes(b"0")
    subprocess.run(
        ["icacls", str(target), "/grant", "*S-1-1-0:(M)"],
        capture_output=True,
        check=True,
        timeout=5,
    )
    backend = PodmanBackend(config(tmp_path))
    monkeypatch.setattr(backend, "_probe", lambda: pytest.fail("unsafe ACL reached engine"))
    assert backend.probe().reason == "ownership_lock_unavailable"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows pinned directory control")
def test_opened_windows_directory_cannot_be_replaced_during_ownership(
    tmp_path: Path, isolated_host_ownership: Path
) -> None:
    backend = PodmanBackend(config(tmp_path))
    with backend._ownership():
        with pytest.raises(OSError):
            isolated_host_ownership.parent.rename(isolated_host_ownership.parent.with_name("moved"))
    isolated_host_ownership.parent.rename(isolated_host_ownership.parent.with_name("moved"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions control")
@pytest.mark.parametrize("leaf", [False, True])
def test_posix_unsafe_directory_or_leaf_mode_rejects(
    tmp_path: Path, isolated_host_ownership: Path, leaf: bool
) -> None:
    isolated_host_ownership.parent.mkdir(mode=0o700)
    target = isolated_host_ownership if leaf else isolated_host_ownership.parent
    if leaf:
        target.write_bytes(b"0")
    target.chmod(0o666 if leaf else 0o777)
    assert PodmanBackend(config(tmp_path)).probe().reason == "ownership_lock_unavailable"
