"""Fault only a disposable operator audit path; restore before cleanup retry."""
import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from quant_terminal.api import create_app
from quant_terminal.sandbox.gateway import IsolationGateway, OperatorIsolationConfig, code_hashes
from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig, _client_environment

PROJECT = Path(__file__).resolve().parents[1]


def main():
    fixture = json.loads(Path(sys.argv[1]).read_text())
    assert fixture["result"] == "passed" and fixture["scope"] == "acceptance_testing"
    assert fixture["source_hashes"] == code_hashes(PROJECT)
    root = Path(fixture["state"]).resolve(strict=True)
    cfg = OperatorIsolationConfig.load(root / "config.json")
    assert cfg.audit_path.parent.resolve(strict=True) == root
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = PROJECT / "docs/validation" / ("m22-gateway-audit-failure-" + stamp + ".json")
    report = {"at": datetime.now(UTC).isoformat(), "state": str(root),
              "source_hashes": code_hashes(PROJECT), "scope": "acceptance_testing",
              "final_release_approved": False}
    backup = root / ("audit-before-fault-" + uuid4().hex + ".jsonl")
    start = threading.Event()
    gateway = IsolationGateway(root, PROJECT, cfg)
    loop = gateway._loop
    gateway._loop = lambda: (start.wait(), loop())
    faulted = False
    failed = False
    try:
        subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"],
                       capture_output=True, check=True, timeout=60)
        with TestClient(create_app(root, PROJECT, isolated_gateway=gateway),
                        base_url="http://127.0.0.1") as client:
            try:
                packet = json.loads((root / "request.json").read_text())
                before_runs = client.get("/runs").json()
                job = client.post("/sandbox/strategies", json=packet).json()
                assert job["status"] == "queued", job
                cfg.audit_path.rename(backup)
                cfg.audit_path.mkdir()
                faulted = True
                start.set()
                deadline = time.monotonic() + 90
                while gateway.failure is None and time.monotonic() < deadline:
                    time.sleep(.1)
                assert gateway.failure is not None
                observed = client.get("/sandbox/jobs/" + job["id"]).json()
                assert observed["status"] == "running" and observed["result"] is None, observed
                assert not client.get("/sandbox/capabilities").json()["isolated_python"]["available"]
                assert client.post("/sandbox/strategies", json=packet).status_code == 422
                assert client.get("/runs").json() == before_runs
                assert gateway.scheduler.lease.handle is not None
                assert gateway.scheduler._owner is not None
                contender = PodmanBackend(PodmanConfig(cfg.image, cfg.seccomp_profile,
                                                     audit_path=root / "competing-audit.jsonl"))
                assert contender.probe().reason == "isolation_busy"
                report.update(job_id=job["id"], status_before_restoration=observed["status"],
                              incomplete_cleanup_lease_retained=True,
                              competing_owner_rejected=True, experiment_published=False)
            finally:
                start.set()
                if faulted:
                    cfg.audit_path.rmdir()  # Verified disposable empty directory; no recursive delete.
                    backup.rename(cfg.audit_path)
                    faulted = False
        completed = gateway.scheduler.store.get(job["id"])
        assert completed["status"] == "interrupted" and completed["result"] is None
        assert gateway.scheduler.lease.handle is None
        report.update(status_after_confirmed_cleanup=completed["status"],
                      restored_audit_path=True, cleanup_completed_before_lease_release=True)
    except Exception as exc:
        failed = True
        report.update(failure_type=type(exc).__name__, failure_reason=str(exc)[:1000])
    finally:
        start.set()
        if faulted:
            cfg.audit_path.rmdir()
            backup.rename(cfg.audit_path)
        try:
            gateway.close()
            inventory = subprocess.run(["podman", "ps", "-aq",
                       "--filter=label=algoalpha.owner=quant-terminal-m16"],
                       capture_output=True, check=True, timeout=15,
                       env=_client_environment()).stdout.decode().split()
            report["container_inventory"] = inventory
            failed = failed or bool(inventory)
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
