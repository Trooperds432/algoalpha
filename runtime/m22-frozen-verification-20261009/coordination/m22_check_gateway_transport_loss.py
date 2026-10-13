"""Lose only the disposable runtime's bounded keeper during a real source job."""
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from quant_terminal.api import create_app
from quant_terminal.sandbox.gateway import IsolationGateway, OperatorIsolationConfig, code_hashes
from quant_terminal.sandbox.podman import _client_environment

PROJECT = Path(__file__).resolve().parents[1]


def main():
    fixture = json.loads(Path(sys.argv[1]).read_text())
    assert fixture["result"] == "passed" and fixture["scope"] == "acceptance_testing"
    assert fixture["source_hashes"] == code_hashes(PROJECT)
    root = Path(fixture["state"]).resolve(strict=True)
    cfg = OperatorIsolationConfig.load(root / "config.json")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    timeout_fixture = len(sys.argv) > 2 and sys.argv[2] == "timeout"
    label = "timeout" if timeout_fixture else "transport-loss"
    target = PROJECT / "docs/validation" / ("m22-gateway-" + label + "-" + stamp + ".json")
    report = {"at": datetime.now(UTC).isoformat(), "state": str(root),
              "source_hashes": code_hashes(PROJECT), "scope": "acceptance_testing",
              "final_release_approved": False}
    gateway = IsolationGateway(root, PROJECT, cfg)
    failed = False

    def inventory():
        return subprocess.run(["podman", "ps", "-aq",
               "--filter=label=algoalpha.owner=quant-terminal-m16"], capture_output=True,
               check=True, timeout=10, env=_client_environment()).stdout.decode().split()

    try:
        subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"],
                       capture_output=True, check=True, timeout=60)
        with TestClient(create_app(root, PROJECT, isolated_gateway=gateway),
                        base_url="http://127.0.0.1") as client:
            packet = json.loads((root / "request.json").read_text())
            baseline = client.get("/runs").json()
            source = "def on_bar(context, bar):\n    while True:\n        pass\n# " + uuid4().hex
            if timeout_fixture:
                source = "def on_bar(context, bar):\n    import time\n    time.sleep(60)\n    return []\n# " + uuid4().hex
            source_hash = hashlib.sha256(source.encode()).hexdigest()
            packet["source_package"] = {"source": source, "source_sha256": source_hash,
                                        "dependencies": []}
            response = client.post("/sandbox/strategies", json=packet)
            assert response.status_code == 202, response.text
            identity = response.json()["id"]
            deadline = time.monotonic() + 90
            observed = []
            while time.monotonic() < deadline:
                events = [json.loads(line) for line in cfg.audit_path.read_text().splitlines()]
                if any(e.get("source_sha256") == source_hash and e["event"] == "launch" for e in events):
                    observed = inventory()
                    if observed:
                        break
                time.sleep(.05)
            assert observed, "Running source container not observed"
            if timeout_fixture:
                inspection = json.loads(subprocess.run(["podman", "inspect", observed[0]],
                             capture_output=True, check=True, timeout=10,
                             env=_client_environment()).stdout)[0]
                assert inspection["Config"]["Timeout"] == 5 and inspection["State"]["Running"]
                report["inspected_timeout_seconds"] = inspection["Config"]["Timeout"]
                report["running_started_at"] = inspection["State"]["StartedAt"]
            keeper = None
            if not timeout_fixture:
                owner = gateway.scheduler._owner
                keeper = owner.keeper._process
                assert keeper is not None and keeper.poll() is None
                # Fault injection acknowledges only this fixed keeper's transport
                # early. No global distro shutdown or another process is touched.
                keeper.stdin.write(b"CLEAN\n")
                keeper.stdin.flush()
                keeper.stdin.close()
                keeper.wait(15)
                assert keeper.returncode == 0
                assert not client.get("/sandbox/capabilities").json()["isolated_python"]["available"]
                assert client.post("/sandbox/strategies", json=packet).status_code == 422
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                job = client.get("/sandbox/jobs/" + identity).json()
                if job["status"] in {"failed", "interrupted", "cancelled"}:
                    break
                time.sleep(.1)
            assert job["status"] == "failed", job
            assert job["result"] is None and client.get("/runs").json() == baseline
            assert not inventory()
            events = [json.loads(line) for line in cfg.audit_path.read_text().splitlines()]
            terminations = [e for e in events if e.get("source_sha256") == source_hash
                            and e["event"] == "termination"]
            assert terminations
            if timeout_fixture:
                # Conmon's own bound can kill before the outer client's wall
                # timer; that remote nonzero exit is reported as exit failure.
                assert any(e.get("reason") in {"wall_timeout", "container_exit_failure"}
                           for e in terminations), terminations
                launch = next(e for e in events if e.get("source_sha256") == source_hash
                              and e["event"] == "launch")
                elapsed = (datetime.fromisoformat(terminations[0]["timestamp"])
                           - datetime.fromisoformat(launch["timestamp"])).total_seconds()
                assert 5 <= elapsed < 30, elapsed
                report["launch_to_termination_seconds"] = elapsed
            report.update(job_id=identity, source_sha256=source_hash,
                          observed_containers=observed, lost_keeper_pid=keeper.pid if keeper else None,
                          terminal_status=job["status"], experiment_published=False,
                          new_admission_rejected=not timeout_fixture,
                          termination_events=terminations, inventory_before_context_close=[])
    except Exception as exc:
        failed = True
        report.update(failure_type=type(exc).__name__, failure_reason=str(exc)[:1000])
    finally:
        try:
            gateway.close()
            remaining = inventory()
            report["container_inventory"] = remaining
            failed = failed or bool(remaining)
        except Exception as exc:
            failed = True
            report["close_failure"] = type(exc).__name__
        report["result"] = "failed" if failed else "passed"
        target.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps({"result": report["result"], "evidence": str(target),
                          "reason": report.get("failure_reason")}), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
