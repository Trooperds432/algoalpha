"""Bounded operator-only real-container research fixture; no broker/forward test."""
import hashlib
import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from quant_terminal.application.isolated_runtime import IsolatedResearchRuntime
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig, _client_environment
from quant_terminal.sandbox.protocol import SandboxBudget
from quant_terminal.sandbox.runtime_owner import IsolationRuntimeOwner
from quant_terminal.sandbox.source_package import SourcePackage
from quant_terminal.sandbox.wsl_keeper import WslKeeper

PROJECT = Path(__file__).resolve().parents[1]
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
state = PROJECT.parents[1] / "runtime" / ("m22-real-runtime-" + stamp + "-" + uuid4().hex[:8])
state.mkdir(parents=True)
report_path = PROJECT / "docs/validation" / ("m22-real-runtime-" + stamp + ".json")
paths = ["application/isolated_runtime.py", "application/runtime.py", "sandbox/podman.py", "sandbox/runtime_owner.py", "sandbox/wsl_keeper.py", "sandbox/source_package.py", "experiments/source.py", "sandbox/strategy.py"]
report = {"at": datetime.now(UTC).isoformat(), "state": str(state), "audit": str(state / "audit.jsonl"), "public_admission_enabled": False, "source_hashes": {p: hashlib.sha256((PROJECT / "src/quant_terminal" / p).read_bytes()).hexdigest() for p in paths}, "result": "pending"}
backend = PodmanBackend(PodmanConfig("sha256:083a4ee8f04d4e4c91bc735931eccd5780c8e4e8ba7acfae04b6234fc573fc29", "/home/user/algoalpha-seccomp.json", audit_path=state / "audit.jsonl"))
owner = IsolationRuntimeOwner(backend, WslKeeper(lifetime_seconds=900))
failed = False
try:
    warm = subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"], capture_output=True, timeout=60, check=True)
    report["guest_python"] = warm.stdout.decode().strip()
    owner.start()
    report["backend_identity"] = json.loads(backend.identity())
    source = "def on_bar(context, bar):\n    return []\n"
    package = SourcePackage(source, hashlib.sha256(source.encode()).hexdigest())
    runtime = IsolatedResearchRuntime(state / "research", PROJECT, backend, package)
    instrument = InstrumentMeta(InstrumentId("ETF-1"), "TEST", "USD", "etf", "XNYS", "America/New_York", Decimal("0.01"), Decimal(1))
    snapshot = runtime.data.import_csv(PROJECT / "examples/data/synthetic_xnys_2026.csv", instrument=instrument, source="synthetic-fixture", publication_delay=timedelta(minutes=1))
    payload = {"instrument_ids": ["ETF-1"], "budget": asdict(SandboxBudget(5, 3, 64_000_000, 4, 32, 0, 0, 4096)), "snapshot_ids": [snapshot.snapshot_id], "start": "2026-11-25T14:30:00+00:00", "end": "2026-11-28T00:00:00+00:00", "currency": "USD", "initial_capital": "10000", "execution": {"commission_per_unit": "0.1"}, "seed": 41}
    config = runtime.prepare(payload)
    first, result = runtime.run(config, "actual-isolated-first")
    second, repeated = runtime.run(config, "actual-isolated-repeat")
    assert result == repeated and first.result_fingerprint == second.result_fingerprint
    assert not result.fills and all(point.nav == Decimal("10000") for point in result.nav)
    assert dict(result.metadata)["isolated_data_slice_count"] == "2"
    assert runtime.registry.get(first.run_id).config == config
    assert runtime.registry.read_artifact(first.run_id, "report-bundle.zip") == runtime.registry.read_artifact(second.run_id, "report-bundle.zip")
    report.update(run_ids=[first.run_id.value, second.run_id.value], config=config.to_dict(), result_fingerprint=first.result_fingerprint, data_slice_count=2, replay="equal_result_and_report_archive", accounting="unchanged_10000_NAV_no_orders", artifacts_verified=True)
except Exception as exc:
    failed = True
    report.update(result="failed", failure_type=type(exc).__name__, failure_reason=str(exc)[:500])
finally:
    if backend._retained:
        try:
            owner.close()
            report["cleanup"] = "confirmed_before_release"
        except Exception as exc:
            failed = True
            report.update(result="failed", cleanup_failure_type=type(exc).__name__, cleanup_failure_reason=str(exc)[:500])
    try:
        inventory = subprocess.run(["podman", "ps", "-aq", "--filter=label=algoalpha.owner=quant-terminal-m16"], capture_output=True, timeout=15, check=True, env=_client_environment()).stdout.decode().split()
        report["post_owner_container_inventory"] = inventory
        if inventory:
            failed = True
            report["result"] = "failed"
    except Exception as exc:
        failed = True
        report.update(result="failed", inventory_failure_type=type(exc).__name__)
    if not failed:
        report["result"] = "passed"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps({"result": report["result"], "evidence": str(report_path), "reason": report.get("failure_reason"), "cleanup": report.get("cleanup")}), flush=True)
raise SystemExit(int(failed))
