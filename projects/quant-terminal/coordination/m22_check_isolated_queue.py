"""Actual bounded operator queue fixture; no broker/forward strategy execution."""
import hashlib
import json
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from quant_terminal.application.isolated_jobs import IsolatedJobScheduler
from quant_terminal.application.runtime import ResearchRuntime
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.experiments import ExperimentStore
from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig, _client_environment
from quant_terminal.sandbox.protocol import SandboxBudget
from quant_terminal.sandbox.runtime_owner import IsolationRuntimeOwner
from quant_terminal.sandbox.wsl_keeper import WslKeeper

PROJECT = Path(__file__).resolve().parents[1]
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
state = PROJECT.parents[1] / "runtime" / ("m22-real-queue-" + stamp + "-" + uuid4().hex[:8])
state.mkdir(parents=True)
audit = state / "audit.jsonl"
target = PROJECT / "docs/validation" / ("m22-real-queue-" + stamp + ".json")
paths = ["application/isolated_jobs.py", "application/jobs.py", "application/isolated_runtime.py", "application/runtime.py", "sandbox/podman.py", "sandbox/runtime_owner.py", "sandbox/wsl_keeper.py", "sandbox/source_package.py", "experiments/source.py", "sandbox/strategy.py"]
report = {"at": datetime.now(UTC).isoformat(), "state": str(state), "audit": str(audit), "public_admission_enabled": False, "source_hashes": {p: hashlib.sha256((PROJECT / "src/quant_terminal" / p).read_bytes()).hexdigest() for p in paths}, "result": "pending"}


def factory():
    backend = PodmanBackend(PodmanConfig("sha256:083a4ee8f04d4e4c91bc735931eccd5780c8e4e8ba7acfae04b6234fc573fc29", "/home/user/algoalpha-seccomp.json", audit_path=audit))
    return IsolationRuntimeOwner(backend, WslKeeper(lifetime_seconds=900))


failed = False
scheduler = IsolatedJobScheduler(state, PROJECT, factory, capacity=2, timeout=120)
try:
    subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"], capture_output=True, timeout=60, check=True)
    runtime = ResearchRuntime(state / "isolated-research", PROJECT)
    instrument = InstrumentMeta(InstrumentId("ETF-1"), "TEST", "USD", "etf", "XNYS", "America/New_York", Decimal(".01"), Decimal(1))
    snapshot = runtime.data.import_csv(PROJECT / "examples/data/synthetic_xnys_2026.csv", instrument=instrument, source="synthetic-fixture", publication_delay=timedelta(minutes=1))
    source = "def on_bar(context, bar):\n    return []\n"
    package = {"source": source, "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "dependencies": []}
    payload = {"instrument_ids": ["ETF-1"], "budget": asdict(SandboxBudget(5, 3, 64_000_000, 4, 32, 0, 0, 4096)), "snapshot_ids": [snapshot.snapshot_id], "start": "2026-11-25T14:30:00+00:00", "end": "2026-11-28T00:00:00+00:00", "currency": "USD", "initial_capital": "10000", "execution": {"commission_per_unit": ".1"}, "seed": 41}
    with scheduler:
        first = scheduler.submit(package, payload)
        second = scheduler.submit(package, payload)
        completed = scheduler.run_next()
        repeated = scheduler.run_next()
        assert completed["id"] == first["id"] and repeated["id"] == second["id"]
        assert completed["status"] == repeated["status"] == "succeeded"
        assert completed["result"]["result_fingerprint"] == repeated["result"]["result_fingerprint"]
        registry = ExperimentStore(state / "isolated-research/runs")
        first_id, second_id = completed["result"]["run_id"], repeated["result"]["run_id"]
        assert registry.get(first_id).config == registry.get(second_id).config
        assert registry.read_artifact(first_id, "report-bundle.zip") == registry.read_artifact(second_id, "report-bundle.zip")
        events = [json.loads(line) for line in audit.read_text().splitlines()]
        assert sum(e["event"] == "runtime_closed" for e in events) == 2
        report.update(job_ids=[first["id"], second["id"]], run_ids=[first_id, second_id], statuses=[completed["status"], repeated["status"]], result_fingerprint=completed["result"]["result_fingerprint"], backend_identity=completed["result"]["config"]["parameters"]["backend_identity"], replay="identical_config_result_and_report", cleanup="two_confirmed_runtime_closes_before_publication", job_events=[scheduler.store.events(first["id"]), scheduler.store.events(second["id"])])
        # Fixed attack fixture remains opaque on the host and runs only inside
        # the pinned container. Observe a real running source container before
        # cancelling; a pre-launch rejection alone is not cancellation evidence.
        spin = "def on_bar(context, bar):\n    while True:\n        pass\n"
        spin_hash = hashlib.sha256(spin.encode()).hexdigest()
        attack = {"source": spin, "source_sha256": spin_hash, "dependencies": []}
        cancellable = scheduler.submit(attack, {**payload, "budget": asdict(SandboxBudget(10, 8, 64_000_000, 4, 32, 0, 0, 4096))})
        outcomes, errors = [], []

        def run_cancel_fixture():
            try:
                outcomes.append(scheduler.run_next())
            except Exception as exc:
                errors.append(type(exc).__name__)

        thread = threading.Thread(target=run_cancel_fixture)
        thread.start()
        launched = False
        deadline = time.monotonic() + 60
        try:
            while time.monotonic() < deadline and thread.is_alive():
                events = [json.loads(line) for line in audit.read_text().splitlines()]
                if any(e.get("source_sha256") == spin_hash and e["event"] == "launch" for e in events):
                    running = subprocess.run(["podman", "ps", "-q", "--filter=label=algoalpha.owner=quant-terminal-m16"], capture_output=True, timeout=10, check=True, env=_client_environment()).stdout.decode().split()
                    if running:
                        launched = True
                        report["cancel_running_container_observed"] = running
                        break
                time.sleep(.1)
            scheduler.cancel(cancellable["id"])
        finally:
            thread.join(180)
        assert not thread.is_alive() and launched and not errors
        assert outcomes[0]["id"] == cancellable["id"] and outcomes[0]["status"] == "cancelled"
        assert len(registry.search()) == 2
        events = [json.loads(line) for line in audit.read_text().splitlines()]
        assert any(e.get("source_sha256") == spin_hash and e["event"] == "termination" and e.get("reason") == "cancelled" for e in events)
        assert sum(e["event"] == "runtime_closed" for e in events) == 3
        report.update(cancel_job_id=cancellable["id"], cancel_status="cancelled_after_confirmed_cleanup", cancelled_source_sha256=spin_hash, cancelled_experiment_published=False, cancel_job_events=scheduler.store.events(cancellable["id"]))
except Exception as exc:
    failed = True
    report.update(result="failed", failure_type=type(exc).__name__, failure_reason=str(exc)[:500])
finally:
    try:
        scheduler.close()
    except Exception as exc:
        failed = True
        report.update(result="failed", close_failure_type=type(exc).__name__, close_failure_reason=str(exc)[:500])
    try:
        inventory = subprocess.run(["podman", "ps", "-aq", "--filter=label=algoalpha.owner=quant-terminal-m16"], capture_output=True, timeout=15, check=True, env=_client_environment()).stdout.decode().split()
        report["post_scheduler_container_inventory"] = inventory
        if inventory:
            failed = True
    except Exception as exc:
        failed = True
        report["inventory_failure_type"] = type(exc).__name__
    report["result"] = "failed" if failed else "passed"
    target.write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps({"result": report["result"], "evidence": str(target), "reason": report.get("failure_reason")}), flush=True)
raise SystemExit(int(failed))
