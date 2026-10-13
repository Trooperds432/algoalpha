"""Opt-in container attacks: source strings are NEVER evaluated on the host."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig, SandboxUnavailable
from quant_terminal.sandbox.protocol import (
    ProtocolError,
    SandboxBudget,
    SandboxRequest,
    SandboxResponse,
    encode_request,
)

_OPT_IN = "RUN_IN_VERIFIED_PODMAN"
_BUDGET = SandboxBudget(5, 3, 64_000_000, 4, 32, 0, 0, 4096)


def _request(
    source: str, *, job_id: str = "hostile-test", budget: SandboxBudget = _BUDGET
) -> bytes:
    return encode_request(
        SandboxRequest(
            job_id,
            source,
            hashlib.sha256(source.encode()).hexdigest(),
            ((("close", "100"),),),
            hashlib.sha256(b'[{"close":"100"}]').hexdigest(),
            (),
            41,
            budget,
        )
    )


@pytest.fixture(scope="session")
def verified_backend(tmp_path_factory: pytest.TempPathFactory) -> PodmanBackend:
    if os.environ.get("QT_M16_HOSTILE") != _OPT_IN:
        pytest.skip("M16 blocked: explicit hostile container opt-in absent")
    image = os.environ.get("QT_M16_IMAGE", "")
    profile = os.environ.get("QT_M16_SECCOMP_PROFILE", "")
    if not image or not profile:
        pytest.skip("M16 blocked: pinned image or VM seccomp profile absent")
    backend = PodmanBackend(
        PodmanConfig(
            image, profile, audit_path=tmp_path_factory.mktemp("m16-audit") / "events.jsonl"
        )
    )
    capability = backend.probe()
    if not capability.available or not capability.engine_version:
        pytest.skip(f"M16 blocked: effective-control probe {capability.reason}")
    return backend


def _events(backend: PodmanBackend, job: str) -> list[dict[str, object]]:
    events = [json.loads(line) for line in _audit_path(backend).read_text().splitlines()]
    return [event for event in events if event.get("job_id") == job]


def _audit_path(backend: PodmanBackend) -> Path:
    path = backend.config.audit_path
    assert path is not None
    return path


def _clean() -> None:
    result = subprocess.run(
        ("podman", "ps", "-aq", "--filter=label=algoalpha.owner=quant-terminal-m16"),
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert not result.stdout.strip(), "sandbox container remained after terminal outcome"


def _run(backend: PodmanBackend, source: str, *, job_id: str) -> SandboxResponse:
    response = backend.execute(_request(source, job_id=job_id))
    _clean()
    return response


def _intent(instrument: str) -> str:
    return repr(
        {
            "instrument_id": instrument,
            "side": "buy",
            "order_type": "market",
            "quantity": "1",
            "limit_price": None,
            "stop_price": None,
        }
    )


def _host_candidates(path: Path) -> tuple[str, ...]:
    resolved = path.resolve()
    candidates = [
        str(resolved),
        "file://" + str(resolved),
        str(resolved.parent / ".." / resolved.parent.name / resolved.name),
    ]
    if os.name == "nt":
        candidates.append(
            f"/mnt/{resolved.drive.rstrip(':').lower()}/{resolved.relative_to(resolved.anchor).as_posix()}"
        )
    return tuple(candidates)


def test_benign_replay_and_provenance(verified_backend: PodmanBackend) -> None:
    source = "def on_bar(context, bar):\n    return [" + _intent("SAFE") + "]\n"
    first = _run(verified_backend, source, job_id="replay")
    second = _run(verified_backend, source, job_id="replay")
    assert first == second and first.status == "ok"
    assert first.intents[0].instrument_id == "SAFE"
    events = _events(verified_backend, "replay")
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert events and all(event.get("source_sha256") == digest for event in events)
    assert all("budget" in event for event in events)


def test_host_canary_paths_environment_proc_and_write_denial(
    verified_backend: PodmanBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "ALG_CANARY_" + secrets.token_hex(32)
    monkeypatch.setenv("ALG_HOST_SECRET", canary)
    host_file = tmp_path / "protected.txt"
    host_file.write_text(canary)
    candidates = _host_candidates(host_file)
    source = (
        "def on_bar(context, bar):\n"
        "    import os\n"
        "    values = list(os.environ.values())\n"
        "    for path in "
        + repr(
            candidates
            + (
                "/proc/self/environ",
                "/proc/self/cmdline",
                "/root/.aws/credentials",
                "/root/.codex/auth.json",
            )
        )
        + ":\n"
        "        try:\n"
        "            with open(path, 'rb') as f:\n"
        "                values.append(f.read(8192).decode(errors='ignore'))\n"
        "        except OSError: pass\n"
        "    if any('ALG_CANARY_' in value for value in values):\n"
        "        return [" + _intent("LEAK") + "]\n"
        "    for path in " + repr(candidates) + ":\n"
        "        try:\n"
        "            with open(path, 'wb') as f: f.write(b'corrupt')\n"
        "            return [" + _intent("WRITE") + "]\n"
        "        except OSError: pass\n"
        "    try:\n"
        "        os.symlink(" + repr(candidates[-1]) + ", '/tmp/link')\n"
        "        return [" + _intent("LINK") + "]\n"
        "    except OSError: return []\n"
    )
    response = _run(verified_backend, source, job_id="canary")
    assert response.status == "ok" and not response.intents
    if host_file.read_text() != canary:
        pytest.fail("protected host canary changed")
    if canary in _audit_path(verified_backend).read_text() + repr(response):
        pytest.fail("canary disclosed in response or audit")


@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_INET6])
@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
def test_network_socket_denied(verified_backend: PodmanBackend, family: int, kind: int) -> None:
    source = (
        "def on_bar(context, bar):\n"
        "    import socket\n"
        "    try:\n"
        f"        s = socket.socket({int(family)}, {int(kind)})\n"
        "        s.close()\n"
        "        return [" + _intent("SOCKET") + "]\n"
        "    except OSError: return []\n"
    )
    response = _run(verified_backend, source, job_id=f"socket-{family}-{kind}")
    assert response.status == "ok" and not response.intents


def test_dns_loopback_ipc_and_ffi_cannot_escape(verified_backend: PodmanBackend) -> None:
    source = (
        "def on_bar(context, bar):\n"
        "    import socket, ctypes\n"
        "    successes = []\n"
        "    try: socket.getaddrinfo('example.invalid', 80); successes.append('dns')\n"
        "    except OSError: pass\n"
        "    try:\n"
        "        socket.create_connection(('127.0.0.1', 3100), timeout=.2)\n"
        "        successes.append('tcp')\n"
        "    except OSError: pass\n"
        "    libc = ctypes.CDLL(None)\n"
        "    if libc.socket(2, 1, 0) >= 0: successes.append('ffi')\n"
        "    try:\n"
        "        s = socket.socket(socket.AF_UNIX); s.connect('/run/podman/podman.sock')\n"
        "        successes.append('ipc')\n"
        "    except OSError: pass\n"
        "    return [" + _intent("ESCAPE") + "] if successes else []\n"
    )
    response = _run(verified_backend, source, job_id="ffi-network")
    assert response.status == "ok" and not response.intents


_ATTACKS = [
    ("cpu", "while True: pass"),
    ("memory", "chunks=[]\n    while True: chunks.append(bytearray(4_000_000))"),
    ("process", "import os,time\n    while True:\n        if os.fork() == 0: time.sleep(60)"),
    ("openfiles", "files=[]\n    while True: files.append(open('/dev/null'))"),
    ("scratch", "with open('/tmp/flood', 'wb') as f: f.write(b'x')"),
    ("stdout", "while True: print('x' * 8192, flush=True)"),
    ("stderr", "import os\n    while True: os.write(2, b'x' * 8192)"),
    ("recursion", "def recurse(): return recurse()\n    recurse()"),
    ("crash", "import os\n    os._exit(71)"),
    (
        "protocol",
        "import os\n    for fd in range(3,32):\n"
        "        try: os.write(fd, b'not-json')\n"
        "        except OSError: pass\n    os._exit(0)",
    ),
]


@pytest.mark.parametrize(("attack", "body"), _ATTACKS)
def test_resource_and_protocol_attacks_are_bounded(
    verified_backend: PodmanBackend, attack: str, body: str
) -> None:
    job = f"limit-{attack}"
    started = time.monotonic()
    try:
        response = _run(
            verified_backend, "def on_bar(context, bar):\n    " + body + "\n", job_id=job
        )
    except SandboxUnavailable:
        pass  # The launch audit below proves this was not an admission rejection.
    else:
        assert response.status == "error", f"{attack} unexpectedly succeeded"
    assert time.monotonic() - started <= _BUDGET.wall_seconds + 20
    events = _events(verified_backend, job)
    assert any(event.get("event") == "launch" for event in events)
    assert any(event.get("event") == "cleanup" for event in events)
    _clean()


@pytest.mark.parametrize("value", ["'NaN'", "'-1'", "'0'", "True", "'9' * 129"])
def test_malformed_intent_rejected(verified_backend: PodmanBackend, value: str) -> None:
    source = (
        "def on_bar(context, bar):\n    intent = "
        + _intent("SAFE")
        + "\n    intent['quantity'] = "
        + value
        + "\n    return [intent]\n"
    )
    response = _run(verified_backend, source, job_id="invalid-intent")
    assert response.status == "error" and not response.intents


@pytest.mark.parametrize("descendants", [False, True])
def test_cancel_reaps_sleeping_strategy_and_descendants(
    verified_backend: PodmanBackend, descendants: bool
) -> None:
    cancel = threading.Event()
    source = "def on_bar(context, bar):\n    import os,time\n"
    if descendants:
        source += "    if os.fork() == 0: time.sleep(60)\n"
    source += "    time.sleep(60)\n"
    job = "cancel-child" if descendants else "cancel"
    observed = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(verified_backend.execute, _request(source, job_id=job), cancel=cancel)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not future.done():
            listing = subprocess.run(
                ("podman", "ps", "-q", f"--filter=label=algoalpha.job={job}"),
                capture_output=True,
                check=True,
                timeout=5,
            ).stdout
            if listing.strip():
                observed = True
                # Give the callback time to create its descendant before cancel.
                time.sleep(0.5)
                break
            time.sleep(0.1)
        cancel.set()
        with pytest.raises(SandboxUnavailable, match="cancel"):
            future.result(timeout=15)
    assert observed, "cancellation fixture did not observe running strategy"
    _clean()


def test_concurrent_attempts_serialize_and_preserve_private_container_state(
    verified_backend: PodmanBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        "def on_bar(context, bar):\n"
        "    import os\n"
        "    if os.path.exists('/tmp/shared'): return [" + _intent("SHARED") + "]\n"
        "    try:\n"
        "        with open('/tmp/shared', 'w') as f: f.write('state')\n"
        "        return [" + _intent("SHARED") + "]\n"
        "    except OSError: return []\n"
    )
    owner = PodmanBackend(verified_backend.config)
    contender = PodmanBackend(verified_backend.config)
    allocated = threading.Event()
    release = threading.Event()
    original_stream = owner._stream

    def gated_start(*args, **kwargs):
        # Pause trusted transport after real creation, enforced-control inspect,
        # watchdog readiness and launch audit. Hostile source remains JSON bytes
        # and is not delivered until ownership contention has been observed.
        if args[0][1:4] == ("start", "--attach", "--interactive"):
            allocated.set()
            assert release.wait(15), "ownership fixture did not release trusted transport"
        return original_stream(*args, **kwargs)

    monkeypatch.setattr(owner, "_stream", gated_start)
    monkeypatch.setattr(
        contender, "_probe", lambda: pytest.fail("busy attempt reached effective probe")
    )
    monkeypatch.setattr(
        contender, "_run", lambda *args: pytest.fail("busy attempt reached Podman command")
    )
    monkeypatch.setattr(
        contender, "_create", lambda *args: pytest.fail("busy attempt reached container launch")
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(owner.execute, _request(source, job_id="concurrent-0"))
        try:
            assert allocated.wait(30), "fixture did not allocate a verified real container"
            assert not first.done(), "first attempt must retain ownership during contention"
            started = time.monotonic()
            second = pool.submit(contender.execute, _request(source, job_id="concurrent-1"))
            with pytest.raises(SandboxUnavailable, match="^isolation_busy$"):
                second.result(timeout=2)
            assert time.monotonic() - started < 2, "busy admission waited for the first job"
            listing = subprocess.run(
                (
                    "podman",
                    "ps",
                    "-a",
                    "--filter=label=algoalpha.owner=quant-terminal-m16",
                    "--format={{.Labels}}",
                ),
                capture_output=True,
                check=True,
                timeout=5,
            ).stdout
            assert b"concurrent-0" in listing and b"concurrent-1" not in listing
            assert len(listing.splitlines()) == 1, "another project container overlapped ownership"
            assert not _events(contender, "concurrent-1"), "busy attempt reached backend admission"
        finally:
            release.set()
        result = first.result(timeout=30)
    assert result.status == "ok" and not result.intents
    assert {"launch", "cleanup", "termination"} <= {
        event["event"] for event in _events(owner, "concurrent-0")
    }
    _clean()
    # A fresh real container succeeds only after the previous owner has cleaned
    # up. The private/read-only namespace cannot carry the first job's state.
    recovered = _run(verified_backend, source, job_id="concurrent-1")
    assert recovered.status == "ok" and not recovered.intents
    assert {"launch", "cleanup", "termination"} <= {
        event["event"] for event in _events(verified_backend, "concurrent-1")
    }


def test_audit_redacts_stdout_and_exception(verified_backend: PodmanBackend) -> None:
    secret = "FAKE_SECRET_" + secrets.token_hex(24)
    source = (
        f"def on_bar(context, bar):\n    print({secret!r})\n    raise RuntimeError({secret!r})\n"
    )
    response = _run(verified_backend, source, job_id="redaction")
    assert response.status == "error"
    if secret in repr(response) + _audit_path(verified_backend).read_text():
        pytest.fail("secret-shaped test marker disclosed")


def test_audit_failure_blocks_admission(verified_backend: PodmanBackend, tmp_path: Path) -> None:
    backend = PodmanBackend(replace(verified_backend.config, audit_path=tmp_path))
    with pytest.raises(SandboxUnavailable, match="audit"):
        backend.execute(_request("def on_bar(context, bar): return []", job_id="audit-failed"))
    _clean()


def test_changed_source_hash_rejects_before_launch(verified_backend: PodmanBackend) -> None:
    raw = json.loads(_request("def on_bar(context, bar): return []", job_id="hash-invalid"))
    raw["source_sha256"] = "0" * 64
    with pytest.raises((ProtocolError, SandboxUnavailable)):
        verified_backend.execute(json.dumps(raw).encode())
    _clean()


def test_abrupt_client_death_reaps_within_three_seconds_and_recovers(
    verified_backend: PodmanBackend, tmp_path: Path
) -> None:
    # This host child only imports the trusted backend. The hostile source is
    # transported as JSON bytes; it is compiled exclusively inside the container.
    child = tmp_path / "trusted_client.py"
    child.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig\n"
        "backend=PodmanBackend(PodmanConfig(sys.argv[1],sys.argv[2],"
        "audit_path=Path(sys.argv[3])))\n"
        "backend.execute(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    source = (
        "def on_bar(context, bar):\n"
        "    import os,time\n"
        "    if os.fork() == 0: time.sleep(60)\n"
        "    time.sleep(60)\n"
    )
    audit = tmp_path / "child-audit.jsonl"
    environment = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    process = subprocess.Popen(
        (
            sys.executable,
            str(child),
            verified_backend.config.image,
            verified_backend.config.seccomp_profile,
            str(audit),
        ),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    assert process.stdin is not None
    process.stdin.write(
        _request(source, job_id="abrupt-death", budget=replace(_BUDGET, wall_seconds=10))
    )
    process.stdin.close()
    launched = False
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline and process.poll() is None:
            result = subprocess.run(
                ("podman", "ps", "-q", "--filter=label=algoalpha.job=abrupt-death"),
                capture_output=True,
                check=True,
                timeout=5,
            )
            if result.stdout.strip():
                launched = True
                break
            time.sleep(0.1)
        assert launched, "client-death fixture never launched: admission is not containment"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
    # Observe immediate parent-death ownership before reconciliation. The ten
    # second conmon deadline cannot satisfy this shorter three-second boundary.
    killed_at = time.monotonic()
    deadline = killed_at + 3
    while time.monotonic() < deadline:
        result = subprocess.run(
            ("podman", "ps", "-q", "--filter=label=algoalpha.job=abrupt-death"),
            capture_output=True,
            check=True,
            timeout=5,
        )
        if not result.stdout.strip():
            break
        time.sleep(0.2)
    assert not result.stdout.strip(), "strategy survived dead client parent-death grace"
    assert time.monotonic() - killed_at <= 3.5

    # Do not reconcile while the detached watchdog is still removing its
    # container. Reconciliation is a recovery action and can race the watchdog,
    # hiding a failed cleanup audit or interrupting its bounded Podman calls.
    observed_parent_death = False
    observed_cleanup = False
    observed_termination = False
    observed_failure = False
    parent_death_elapsed: float | None = None
    audit_deadline = time.monotonic() + 45
    while time.monotonic() < audit_deadline:
        try:
            audit_text = audit.read_text()
        except FileNotFoundError:
            audit_text = ""
        lines = audit_text.splitlines()
        records: list[dict[str, object]] = []
        for index, line in enumerate(lines):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not audit_text.endswith("\n"):
                    break  # The detached watchdog may still be writing this record.
                raise
        events = {record.get("event") for record in records}
        if "watchdog_parent_death" in events and not observed_parent_death:
            observed_parent_death = True
            parent_death_elapsed = time.monotonic() - killed_at
        observed_cleanup = "cleanup" in events
        observed_termination = "termination" in events
        observed_failure = "watchdog_failure" in events
        if observed_failure or (
            observed_parent_death and observed_cleanup and observed_termination
        ):
            break
        time.sleep(0.1)
    pending_before_reconcile = tuple(audit.parent.glob(audit.name + ".*.pending"))
    health_marker_before_reconcile = audit.with_suffix(".unhealthy").exists()
    verified_backend.reconcile()
    _clean()
    assert observed_parent_death and parent_death_elapsed is not None
    assert parent_death_elapsed <= 3.5, "watchdog parent-death event exceeded its deadline"
    assert observed_cleanup, "watchdog did not audit completed container cleanup"
    assert observed_termination, "watchdog did not audit terminal completion"
    assert not observed_failure, "watchdog recorded a cleanup failure"
    assert not pending_before_reconcile, "watchdog left an active lease after completion"
    assert not health_marker_before_reconcile, "watchdog left durable cleanup unhealthy state"


def test_child_process_inherits_network_and_host_file_denial(
    verified_backend: PodmanBackend, tmp_path: Path
) -> None:
    canary_file = tmp_path / "child-protected.txt"
    canary_file.write_bytes(b"protected")
    child_code = (
        "import socket,sys\n"
        "escaped=False\n"
        "try:\n s=socket.socket(); s.close(); escaped=True\n"
        "except OSError: pass\n"
        f"try:\n open({_host_candidates(canary_file)[-1]!r}, 'rb'); escaped=True\n"
        "except OSError: pass\n"
        "sys.exit(42 if escaped else 0)\n"
    )
    source = (
        "def on_bar(context, bar):\n"
        "    import subprocess,sys\n"
        f"    child = subprocess.run([sys.executable,'-I','-S','-c',{child_code!r}])\n"
        "    if child.returncode == 42: return [" + _intent("CHILD_ESCAPE") + "]\n"
        "    if child.returncode != 0: raise RuntimeError('child probe did not run')\n"
        "    return []\n"
    )
    response = _run(verified_backend, source, job_id="child-network")
    assert response.status == "ok" and not response.intents
    assert canary_file.read_bytes() == b"protected"
