"""Operator-only runtime integration; protocol fake is not isolation evidence."""

import hashlib
import io
import threading
import zipfile
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quant_terminal.application.isolated_runtime import IsolatedResearchRuntime
from quant_terminal.application.runtime import ResearchRuntime
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.experiments import IntegrityError
from quant_terminal.sandbox.protocol import (
    SandboxBudget,
    SandboxResponse,
    WireIntent,
    decode_request,
)
from quant_terminal.sandbox.source_package import SourcePackage

PROJECT = Path(__file__).parents[2]
BUDGET = SandboxBudget(5, 3, 64_000_000, 4, 32, 0, 0, 4096)


class ProtocolBackend:
    def __init__(self) -> None:
        self.version = "fixed-test-backend"
        self.requests = []
        self.cancel_seen = None
        self.expected_seen = None
        self.change_before_execute = False

    def identity(self) -> str:
        return self.version

    def execute(
        self,
        blob: bytes,
        cancel: threading.Event | None = None,
        *,
        expected_identity: str | None = None,
    ) -> SandboxResponse:
        self.cancel_seen = cancel
        self.expected_seen = expected_identity
        if self.change_before_execute:
            self.version = "changed-before-execute"
        if expected_identity is not None and expected_identity != self.version:
            raise IntegrityError("backend identity mismatch")
        if cancel is not None and cancel.is_set():
            raise DomainError("cancelled")
        request = decode_request(blob)
        self.requests.append(request)
        row = dict(request.data_rows[-1])
        intents = ()
        if row["position"] == "0" and row["has_active_order"] == "False":
            intents = (WireIntent(row["instrument_id"], "buy", "market", "10"),)
        return SandboxResponse(request.job_id, "ok", intents)


def setup(tmp_path: Path):
    backend = ProtocolBackend()
    event = threading.Event()
    source = "opaque invalid Python: must never compile on host"
    package = SourcePackage(source, hashlib.sha256(source.encode()).hexdigest())
    runtime = IsolatedResearchRuntime(tmp_path / "state", PROJECT, backend, package, cancel=event)
    instrument = InstrumentMeta(
        InstrumentId("ETF-1"),
        "TEST",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal("0.01"),
        Decimal(1),
    )
    snapshot = runtime.data.import_csv(
        PROJECT / "examples/data/synthetic_xnys_2026.csv",
        instrument=instrument,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    request = {
        "snapshot_ids": [snapshot.snapshot_id],
        "instrument_ids": ["ETF-1"],
        "budget": asdict(BUDGET),
        "start": "2026-11-25T14:30:00+00:00",
        "end": "2026-11-28T00:00:00+00:00",
        "currency": "USD",
        "initial_capital": "10000",
        "execution": {"commission_per_unit": "0.1"},
        "seed": 41,
    }
    return runtime, backend, event, request


def test_isolated_runtime_uses_real_engine_and_canonical_store_without_host_exec(tmp_path):
    runtime, backend, event, request = setup(tmp_path)
    config = runtime.prepare(request)
    assert not backend.requests
    first, result = runtime.run(config, "isolated-first")
    second, repeated = runtime.run(config, "isolated-repeat")
    assert result == repeated and first.result_fingerprint == second.result_fingerprint
    assert result.fills and result.commission_cost == Decimal(1)
    assert backend.cancel_seen is event
    assert backend.expected_seen == "fixed-test-backend"
    assert backend.requests and all(r.seed == 41 and r.budget == BUDGET for r in backend.requests)
    for request in backend.requests:
        current = dict(request.data_rows[-1])["as_of"]
        assert all(dict(row)["available_at"] <= current for row in request.data_rows)
    source = runtime.registry.read_artifact(first.run_id, "strategy-source.py")
    with zipfile.ZipFile(
        io.BytesIO(runtime.registry.read_artifact(first.run_id, "source-bundle.zip"))
    ) as archive:
        assert archive.read("submitted-strategy.py") == source == runtime.package.source.encode()
    assert (
        runtime.registry.get(first.run_id).config.parameters["backend_identity"] == backend.version
    )
    assert runtime.registry.read_artifact(
        first.run_id, "report-bundle.zip"
    ) == runtime.registry.read_artifact(second.run_id, "report-bundle.zip")


def test_backend_or_source_changes_reject_before_execution_or_registration(tmp_path):
    runtime, backend, _, request = setup(tmp_path)
    config = runtime.prepare(request)
    backend.version = "changed"
    with pytest.raises(IntegrityError, match="backend"):
        runtime.run(config, "changed-backend")
    assert not backend.requests and not runtime.registry.search()
    backend.version = "fixed-test-backend"
    with pytest.raises(IntegrityError, match="source"):
        runtime.run(replace(config, strategy_source_hash="0" * 64), "changed-source")
    assert not backend.requests and not runtime.registry.search()


def test_pre_cancel_rejects_and_trusted_mode_remains_separate(tmp_path):
    runtime, backend, event, request = setup(tmp_path)
    config = runtime.prepare(request)
    event.set()
    with pytest.raises(DomainError, match="cancel"):
        runtime.run(config, "cancelled")
    assert not backend.requests and not runtime.registry.search()
    trusted = ResearchRuntime(tmp_path / "trusted", PROJECT)
    with pytest.raises(DomainError):
        trusted.prepare(request)


def test_identity_is_bound_inside_execution_before_source_reaches_worker(tmp_path):
    runtime, backend, _, request = setup(tmp_path)
    config = runtime.prepare(request)
    backend.change_before_execute = True
    with pytest.raises(IntegrityError, match="backend"):
        runtime.run(config, "late-backend-change")
    assert backend.expected_seen == config.parameters["backend_identity"]
    assert not backend.requests and not runtime.registry.search()


@pytest.mark.parametrize(
    "update",
    [
        {"source_path": "C:/private.py"},
        {"verified": True},
        {"seed": True},
        {"seed": 2**63},
        {"instrument_ids": ["UNKNOWN"]},
        {"instrument_ids": ["ETF-1", "ETF-1"]},
        {"history_count": 0},
        {"budget": asdict(replace(BUDGET, scratch_bytes=1))},
        {"budget": asdict(replace(BUDGET, cpu_seconds=6))},
    ],
)
def test_invalid_isolated_request_rejects_without_protocol_execution(tmp_path, update):
    runtime, backend, _, request = setup(tmp_path)
    with pytest.raises(DomainError):
        runtime.prepare({**request, **update})
    assert not backend.requests and not runtime.registry.search()
