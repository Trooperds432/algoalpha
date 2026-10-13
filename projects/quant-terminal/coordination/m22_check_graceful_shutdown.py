"""Actual ASGI lifespan shutdown while a disposable isolated source is running.

The trusted child receives GRACEFUL on stdin and sets uvicorn.should_exit.
This is a controlled normal-lifespan trigger, not an OS signal-delivery test.
No force kill is used to establish a passing graceful outcome.
"""

import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from quant_terminal.sandbox.gateway import code_hashes

PROJECT = Path(__file__).resolve().parents[1]


def child(root, port, outcome):
    import uvicorn

    from quant_terminal.api import create_app
    from quant_terminal.sandbox.gateway import IsolationGateway, OperatorIsolationConfig

    gateway = IsolationGateway(root, PROJECT, OperatorIsolationConfig.load(root / "config.json"))
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(root, PROJECT, isolated_gateway=gateway),
            host="127.0.0.1",
            port=port,
        )
    )
    trigger = {}

    def command():
        if sys.stdin.buffer.readline() == b"GRACEFUL\n":
            trigger["requested_at"] = datetime.now(UTC).isoformat()
            server.should_exit = True

    threading.Thread(target=command, daemon=True, name="trusted-graceful-trigger").start()
    server.run()
    scheduler = gateway.scheduler
    Path(outcome).write_text(
        json.dumps(
            {
                "returned_at": datetime.now(UTC).isoformat(),
                "trigger": trigger,
                "server_started": server.started,
                "gateway_failure": gateway.failure,
                "gateway_thread_alive": gateway.thread is not None and gateway.thread.is_alive(),
                "scheduler_failure": None if scheduler is None else scheduler.failure,
                "scheduler_started": None if scheduler is None else scheduler._started,
                "owner_retained": scheduler is not None and scheduler._owner is not None,
                "active_retained": scheduler is not None and scheduler._active is not None,
                "scheduler_lease_retained": scheduler is not None
                and scheduler.lease.handle is not None,
            },
            indent=2,
        ),
        encoding="utf8",
    )


def main():
    from quant_terminal.application.jobs import JobStore, SchedulerLease
    from quant_terminal.experiments import ExperimentStore, RunNotFoundError
    from quant_terminal.sandbox.gateway import OperatorIsolationConfig
    from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig, _client_environment

    fixture = json.loads(Path(sys.argv[1]).read_text())
    assert fixture["result"] == "passed" and fixture["scope"] == "acceptance_testing"
    assert fixture["source_hashes"] == code_hashes(PROJECT)
    root = Path(fixture["state"]).resolve(strict=True)
    cfg = OperatorIsolationConfig.load(root / "config.json")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = PROJECT / "docs/validation" / ("m22-graceful-shutdown-" + stamp + ".json")
    outcome = root / ("graceful-child-" + stamp + ".json")
    logfile = root / ("graceful-child-" + stamp + ".log")
    report = {
        "at": datetime.now(UTC).isoformat(),
        "state": str(root),
        "source_hashes": code_hashes(PROJECT),
        "scope": "acceptance_testing",
        "final_release_approved": False,
        "trigger": "trusted stdin GRACEFUL sets uvicorn.Server.should_exit=True",
        "os_signal_delivery_tested": False,
        "force_kill_used": False,
        "child_outcome_path": str(outcome),
        "child_log_path": str(logfile),
    }
    with socket.socket() as allocator:
        allocator.bind(("127.0.0.1", 0))
        port = allocator.getsockname()[1]
    base = "http://127.0.0.1:" + str(port)
    process = None
    failed = False
    triggered = False
    log = logfile.open("ab")

    def request(path, value=None):
        data = None if value is None else json.dumps(value).encode()
        req = urllib.request.Request(
            base + path, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    def inventory():
        return (
            subprocess.run(
                ["podman", "ps", "-aq", "--filter=label=algoalpha.owner=quant-terminal-m16"],
                capture_output=True,
                check=True,
                timeout=10,
                env=_client_environment(),
            )
            .stdout.decode()
            .split()
        )

    def graceful():
        nonlocal triggered
        if process is not None and process.poll() is None and not triggered:
            process.stdin.write(b"GRACEFUL\n")
            process.stdin.flush()
            triggered = True

    try:
        subprocess.run(
            ["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"],
            capture_output=True,
            check=True,
            timeout=60,
        )
        assert not inventory(), "Pre-existing containers block exclusive test"
        process = subprocess.Popen(
            [sys.executable, __file__, "--child", str(root), str(port), str(outcome)],
            cwd=PROJECT,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
        )
        report["api_pid"] = process.pid
        deadline = time.monotonic() + 90
        ready = False
        while time.monotonic() < deadline:
            assert process.poll() is None, "Disposable API exited during startup"
            try:
                if request("/sandbox/capabilities")["isolated_python"]["available"]:
                    ready = True
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        assert ready, "Disposable API startup timed out"
        registry = ExperimentStore(root / "runs")
        previous_runs = [m.to_dict() for m in registry.search()]
        packet = json.loads((root / "request.json").read_text())
        source = (
            "def on_bar(context, bar):\n    import time\n    time.sleep(60)\n    return []\n# "
            + uuid4().hex
        )
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        packet["source_package"] = {
            "source": source,
            "source_sha256": source_hash,
            "dependencies": [],
        }
        identity = request("/sandbox/strategies", packet)["id"]
        report.update(job_id=identity, source_sha256=source_hash)
        deadline = time.monotonic() + 90
        observed = []
        while time.monotonic() < deadline:
            events = [json.loads(line) for line in cfg.audit_path.read_text().splitlines()]
            if any(
                e.get("source_sha256") == source_hash and e["event"] == "launch" for e in events
            ):
                candidates = inventory()
                if candidates:
                    inspected = json.loads(
                        subprocess.run(
                            ["podman", "inspect", candidates[0]],
                            capture_output=True,
                            check=True,
                            timeout=10,
                            env=_client_environment(),
                        ).stdout
                    )[0]
                    if inspected["State"]["Running"]:
                        observed = candidates
                        report["running_started_at"] = inspected["State"]["StartedAt"]
                        break
            if request("/sandbox/jobs/" + identity)["status"] in {
                "failed",
                "cancelled",
                "succeeded",
            }:
                break
            time.sleep(0.05)
        assert observed, "Running source container not observed before graceful trigger"
        report["observed_running_containers"] = observed
        graceful()
        process.wait(60)
        report["api_exit_code"] = process.returncode
        assert process.returncode == 0, "Disposable API did not exit normally"
        child_result = json.loads(outcome.read_text())
        report["child_outcome"] = child_result
        assert child_result["trigger"] and child_result["server_started"]
        assert child_result["gateway_failure"] is None and child_result["scheduler_failure"] is None
        for key in (
            "gateway_thread_alive",
            "scheduler_started",
            "owner_retained",
            "active_retained",
            "scheduler_lease_retained",
        ):
            assert child_result[key] is False, key
        assert not inventory(), "Container remained after normal lifespan shutdown"
        report["inventory_before_lease_checks"] = []
        store = JobStore(root / "isolated-jobs")
        job = store.get(identity)
        assert job["status"] == "interrupted" and job["result"] is None
        assert [m.to_dict() for m in registry.search()] == previous_runs
        try:
            registry.get("run-" + identity)
        except RunNotFoundError:
            pass
        else:
            raise AssertionError("Gracefully interrupted source published an experiment")
        events = [json.loads(line) for line in cfg.audit_path.read_text().splitlines()]
        launches = [
            e for e in events if e.get("source_sha256") == source_hash and e["event"] == "launch"
        ]
        cleanup = [
            e for e in events if e.get("source_sha256") == source_hash and e["event"] == "cleanup"
        ]
        termination = [
            e
            for e in events
            if e.get("source_sha256") == source_hash and e["event"] == "termination"
        ]
        assert len(launches) == 1 and cleanup and termination
        assert any(e.get("reason") == "cancelled" for e in termination), termination
        assert datetime.fromisoformat(cleanup[-1]["timestamp"]) <= datetime.fromisoformat(
            job["updated_at"]
        )
        lease = SchedulerLease(store.root / "isolated-scheduler.lock")
        lease.acquire()
        lease.release()
        backend = PodmanBackend(
            PodmanConfig(cfg.image, cfg.seccomp_profile, audit_path=cfg.audit_path)
        )
        with backend._ownership():
            pass
        report.update(
            terminal_status=job["status"],
            terminal_updated_at=job["updated_at"],
            result_is_none=True,
            experiment_published=False,
            launch_count=len(launches),
            cleanup_events=cleanup,
            termination_events=termination,
            scheduler_lease_reacquired_after_empty_inventory=True,
            host_ownership_reacquired_after_empty_inventory=True,
            cleanup_before_terminal_status=True,
        )
    except Exception as exc:
        failed = True
        report.update(failure_type=type(exc).__name__, failure_reason=str(exc)[:1000])
    finally:
        if process is not None and process.poll() is None:
            try:
                graceful()
                process.wait(60)
            except Exception as exc:
                failed = True
                report["normal_cleanup_failure"] = type(exc).__name__
                report["live_child_pid"] = process.pid
        log.close()
        try:
            remaining = inventory()
            report["final_container_inventory"] = remaining
            failed = failed or bool(remaining)
        except Exception as exc:
            failed = True
            report["inventory_failure"] = type(exc).__name__
        report["result"] = "failed" if failed else "passed"
        target.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(
            json.dumps(
                {
                    "result": report["result"],
                    "evidence": str(target),
                    "reason": report.get("failure_reason"),
                }
            ),
            flush=True,
        )
    return int(failed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit(main())
