"""Real operator acceptance-testing gateway: HTTP adapter and subprocess CLI.

Historical proof inputs permit only a bounded integration test, not final release
approval. No operator configuration is installed in the default terminal.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from quant_terminal import __version__
from quant_terminal.api import create_app
from quant_terminal.application.runtime import ResearchRuntime
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.sandbox.gateway import (
    PUBLIC_BUDGET_CAPS, IsolationGateway, OperatorIsolationConfig, code_hashes, host_identity,
)
from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig, _client_environment
from quant_terminal.sandbox.runtime_owner import IsolationRuntimeOwner
from quant_terminal.sandbox.wsl_keeper import WslKeeper

PROJECT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:083a4ee8f04d4e4c91bc735931eccd5780c8e4e8ba7acfae04b6234fc573fc29"
PROFILE = "/home/user/algoalpha-seccomp.json"


def main():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = PROJECT.parents[1] / "runtime" / ("m22-gateway-" + stamp + "-" + uuid4().hex[:8])
    root.mkdir(parents=True)
    target = PROJECT / "docs/validation" / ("m22-real-gateway-" + stamp + ".json")
    report = {"at": datetime.now(UTC).isoformat(), "state": str(root),
              "scope": "acceptance_testing", "final_release_approved": False,
              "default_admission_enabled": False, "source_hashes": code_hashes(PROJECT)}
    cfg = OperatorIsolationConfig(IMAGE, PROFILE, root / "audit.jsonl", root / "receipt.json")
    failed = False
    try:
        subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"],
                       capture_output=True, check=True, timeout=60)
        owner = IsolationRuntimeOwner(PodmanBackend(PodmanConfig(IMAGE, PROFILE,
                                      audit_path=cfg.audit_path)), WslKeeper(lifetime_seconds=900))
        try:
            owner.start()
            identity = json.loads(owner.backend.identity())
        finally:
            owner.close()
        proofs = {}
        for label, filename in {
            "hostile": "m22-hostile-current-20261007T195336Z.json",
            "lifecycle": "m22-real-queue-20261007T194838Z.json",
            "review": "m22-runtime-integration-20261007.md",
        }.items():
            proof = root / (label + Path(filename).suffix)
            shutil.copyfile(PROJECT / "docs/validation" / filename, proof)
            proofs[label] = {"path": proof.name,
                             "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}
        receipt = {"schema_version": 1, "approved": True, "scope": "acceptance_testing",
                   "engine_version": __version__, "host": host_identity(),
                   "source_hashes": code_hashes(PROJECT), "backend_identity": identity,
                   "dependency_lock_sha256": hashlib.sha256(
                       (PROJECT / "requirements-dev.lock").read_bytes()).hexdigest(),
                   "evidence": proofs}
        cfg.acceptance_path.write_text(json.dumps(receipt), encoding="utf8")
        config_file = root / "config.json"
        config_file.write_text(json.dumps({"image": IMAGE, "seccomp_profile": PROFILE,
                                          "audit_path": str(cfg.audit_path),
                                          "acceptance_path": str(cfg.acceptance_path)}))
        runtime = ResearchRuntime(root, PROJECT)
        instrument = InstrumentMeta(InstrumentId("ETF-1"), "TEST", "USD", "etf", "XNYS",
                                    "America/New_York", Decimal(".01"), Decimal(1))
        snapshot = runtime.data.import_csv(PROJECT / "examples/data/synthetic_xnys_2026.csv",
                    instrument=instrument, source="synthetic-fixture",
                    publication_delay=timedelta(minutes=1))
        source = "def on_bar(context, bar):\n    return []\n"
        packet = {"execution_mode": "isolated_python", "source_package": {
                  "source": source, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                  "dependencies": []}, "request": {"instrument_ids": ["ETF-1"],
                  "budget": asdict(PUBLIC_BUDGET_CAPS), "snapshot_ids": [snapshot.snapshot_id],
                  "start": "2026-11-25T14:30:00+00:00", "end": "2026-11-28T00:00:00+00:00",
                  "currency": "USD", "initial_capital": "10000",
                  "execution": {"commission_per_unit": ".1"}, "seed": 41}}
        gateway = IsolationGateway(root, PROJECT, cfg)
        with TestClient(create_app(root, PROJECT, isolated_gateway=gateway),
                        base_url="http://127.0.0.1") as client:
            cap = client.get("/sandbox/capabilities").json()["isolated_python"]
            assert cap["available"] and cap["scope"] == "acceptance_testing"
            response = client.post("/sandbox/strategies", json=packet)
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            assert source not in response.text
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                response = client.get("/sandbox/jobs/" + job_id)
                completed = response.json()
                if completed["status"] in {"succeeded", "failed", "interrupted"}:
                    break
                time.sleep(.1)
            assert completed["status"] == "succeeded", completed
            assert source not in response.text
            detail = client.get("/runs/" + completed["result"]["run_id"])
            assert detail.status_code == 200
            assert client.get("/jobs/" + job_id).status_code == 404
            assert client.post("/backtests", json=packet).status_code == 422
            report.update(http_job_id=job_id, http_run_id=completed["result"]["run_id"],
                          http_status=completed["status"], http_result=completed["result"])
            spin = "def on_bar(context, bar):\n    while True:\n        pass\n"
            spin_hash = hashlib.sha256(spin.encode()).hexdigest()
            attack = {**packet, "source_package": {"source": spin,
                      "source_sha256": spin_hash, "dependencies": []}}
            response = client.post("/sandbox/strategies", json=attack)
            assert response.status_code == 202, response.text
            cancel_id = response.json()["id"]
            observed = []
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                events = [json.loads(line) for line in cfg.audit_path.read_text().splitlines()]
                if any(e.get("source_sha256") == spin_hash and e["event"] == "launch" for e in events):
                    observed = subprocess.run(["podman", "ps", "-q",
                               "--filter=label=algoalpha.owner=quant-terminal-m16"],
                               capture_output=True, check=True, timeout=10,
                               env=_client_environment()).stdout.decode().split()
                    if observed:
                        break
                if client.get("/sandbox/jobs/" + cancel_id).json()["status"] in {"failed", "cancelled"}:
                    break
                time.sleep(.05)
            assert observed, "Real running source container not observed before cancel"
            client.post("/sandbox/jobs/" + cancel_id + "/cancel", json={}).raise_for_status()
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                cancelled = client.get("/sandbox/jobs/" + cancel_id).json()
                if cancelled["status"] in {"failed", "cancelled", "interrupted"}:
                    break
                time.sleep(.1)
            assert cancelled["status"] == "cancelled", cancelled
            assert len(client.get("/runs").json()) == 1
            events = [json.loads(line) for line in cfg.audit_path.read_text().splitlines()]
            assert any(e.get("source_sha256") == spin_hash and e["event"] == "termination"
                       and e.get("reason") == "cancelled" for e in events)
            report.update(cancel_job_id=cancel_id, cancel_status="cancelled_after_cleanup",
                          cancel_observed_containers=observed, cancelled_experiment_published=False)
        request = root / "request.json"
        request.write_text(json.dumps(packet), encoding="utf8")
        command = [sys.executable, "-m", "quant_terminal", "--state-dir", str(root),
                   "--project-root", str(PROJECT), "--isolation-config", str(config_file),
                   "sandbox-run", "--request", str(request)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=200)
        report.update(cli_exit_code=result.returncode, cli_stderr=result.stderr[-2000:])
        assert result.returncode == 0, result.stderr
        cli = json.loads(result.stdout)
        assert cli["status"] == "succeeded" and source not in result.stdout
        assert cli["result"]["result_fingerprint"] == completed["result"]["result_fingerprint"]
        report.update(cli_job_id=cli["id"], cli_run_id=cli["result"]["run_id"],
                      replay="HTTP_and_subprocess_CLI_identical_result_fingerprint")
    except Exception as exc:
        failed = True
        report.update(failure_type=type(exc).__name__, failure_reason=str(exc)[:1000])
    finally:
        try:
            inventory = subprocess.run(["podman", "ps", "-aq",
                       "--filter=label=algoalpha.owner=quant-terminal-m16"],
                       capture_output=True, timeout=15, check=True, env=_client_environment())
            report["container_inventory"] = inventory.stdout.decode().split()
            failed = failed or bool(report["container_inventory"])
        except Exception as exc:
            failed = True
            report["inventory_failure"] = type(exc).__name__
        report["result"] = "failed" if failed else "passed"
        target.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps({"result": report["result"], "evidence": str(target),
                          "reason": report.get("failure_reason")}), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
