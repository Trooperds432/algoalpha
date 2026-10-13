"""Operator receipt and public transport contracts; fake is not containment proof."""

import hashlib
import json
import time
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_terminal import __version__
from quant_terminal.api import create_app
from quant_terminal.application.runtime import ResearchRuntime
from quant_terminal.cli import main
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.sandbox.gateway import (
    PUBLIC_BUDGET_CAPS,
    IsolationGateway,
    OperatorIsolationConfig,
    code_hashes,
    host_identity,
)
from quant_terminal.sandbox.podman import SandboxUnavailable
from quant_terminal.sandbox.protocol import SandboxResponse, decode_request

PROJECT = Path(__file__).parents[2]
IDENTITY = {"backend": "podman", "engine_version": "fixture", "image_id": "a" * 64}


class Backend:
    def identity(self):
        return json.dumps(IDENTITY)

    def execute(self, blob, cancel=None, *, expected_identity=None):
        assert json.loads(expected_identity) == IDENTITY
        if cancel is not None and cancel.is_set():
            raise DomainError("fixture cancelled")
        request = decode_request(blob)
        return SandboxResponse(request.job_id, "ok", ())


class Owner:
    def __init__(self):
        self.backend = Backend()
        self.ready = False

    def start(self):
        self.ready = True

    def ensure_available(self):
        if not self.ready:
            raise DomainError("fixture owner unavailable")

    def close(self):
        self.ready = False


def setup(tmp_path, monkeypatch):
    root = tmp_path / "state"
    runtime = ResearchRuntime(root, PROJECT)
    instrument = InstrumentMeta(
        InstrumentId("ETF-1"),
        "TEST",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal(".01"),
        Decimal(1),
    )
    snapshot = runtime.data.import_csv(
        PROJECT / "examples/data/synthetic_xnys_2026.csv",
        instrument=instrument,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    proofs = {}
    for label in ("hostile", "lifecycle", "review"):
        proof = tmp_path / (label + ".json")
        proof.write_text(json.dumps({"fixture": label, "not_containment_evidence": True}))
        proofs[label] = {
            "path": proof.name,
            "sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
        }
    receipt = {
        "schema_version": 1,
        "approved": True,
        "scope": "acceptance_testing",
        "engine_version": __version__,
        "host": host_identity(),
        "source_hashes": code_hashes(PROJECT),
        "dependency_lock_sha256": hashlib.sha256(
            (PROJECT / "requirements-dev.lock").read_bytes()
        ).hexdigest(),
        "backend_identity": IDENTITY,
        "evidence": proofs,
    }
    approval = tmp_path / "testing-receipt.json"
    approval.write_text(json.dumps(receipt))
    config = OperatorIsolationConfig(
        "sha256:" + "a" * 64, "/fixture-profile", tmp_path / "audit.jsonl", approval
    )
    gateway = IsolationGateway(root, PROJECT, config)
    monkeypatch.setattr(gateway, "_owner", lambda: Owner())
    source = "opaque invalid Python never executed on host"
    packet = {
        "execution_mode": "isolated_python",
        "source_package": {
            "source": source,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "dependencies": [],
        },
        "request": {
            "instrument_ids": ["ETF-1"],
            "budget": asdict(PUBLIC_BUDGET_CAPS),
            "snapshot_ids": [snapshot.snapshot_id],
            "start": "2026-11-25T14:30:00+00:00",
            "end": "2026-11-28T00:00:00+00:00",
            "currency": "USD",
            "initial_capital": "10000",
            "execution": {"commission_per_unit": ".1"},
            "seed": 41,
        },
    }
    return root, gateway, packet, receipt


def test_default_api_and_cli_reject_before_source_file_read_or_staging(
    tmp_path, monkeypatch, capsys
):
    with TestClient(
        create_app(tmp_path / "default", PROJECT), base_url="http://127.0.0.1"
    ) as client:
        assert client.get("/sandbox/capabilities").json()["isolated_python"]["available"] is False
        response = client.post(
            "/sandbox/strategies", content="{", headers={"content-type": "application/json"}
        )
        assert response.status_code == 422 and "isolation_backend_unverified" in response.text
        assert not (tmp_path / "default/isolated-jobs").exists()
    assert (
        main(
            [
                "--state-dir",
                str(tmp_path / "cli-default"),
                "sandbox-run",
                "--request",
                str(tmp_path / "does-not-exist.py"),
            ]
        )
        == 1
    )
    assert "isolation_backend_unverified" in capsys.readouterr().err
    assert not (tmp_path / "cli-default").exists()


def test_configured_api_runs_distinct_queue_and_does_not_echo_source(tmp_path, monkeypatch):
    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with TestClient(
        create_app(root, PROJECT, isolated_gateway=gateway), base_url="http://127.0.0.1"
    ) as client:
        capability = client.get("/sandbox/capabilities").json()["isolated_python"]
        assert capability["available"] and capability["scope"] == "acceptance_testing"
        response = client.post("/sandbox/strategies", json=packet)
        assert response.status_code == 202, response.text
        identity = response.json()["id"]
        assert packet["source_package"]["source"] not in response.text
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = client.get("/sandbox/jobs/" + identity)
            job = response.json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert job["status"] == "succeeded", job
        assert packet["source_package"]["source"] not in response.text
        assert client.get("/jobs/" + identity).status_code == 404
        assert client.get("/runs/" + job["result"]["run_id"]).status_code == 200
        assert client.get("/sandbox/jobs/" + identity + "/events").status_code == 200
        assert (
            client.post("/sandbox/jobs/" + identity + "/cancel", json={}).json()["status"]
            == "succeeded"
        )
        assert client.post("/backtests", json=packet).status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved", False),
        ("engine_version", "changed"),
        ("host", {}),
        ("source_hashes", {}),
        ("dependency_lock_sha256", "0" * 64),
        ("scope", ["invalid"]),
        ("backend_identity", {}),
    ],
)
def test_operator_receipt_incompatibility_rejects_before_claim(tmp_path, monkeypatch, field, value):
    _, gateway, _, receipt = setup(tmp_path, monkeypatch)
    receipt[field] = value
    gateway.config.acceptance_path.write_text(json.dumps(receipt))
    with pytest.raises(DomainError):
        with gateway:
            pass
    if gateway.scheduler is not None:
        assert not gateway.scheduler.store.list()


def test_request_cannot_supply_operator_paths_flags_or_larger_caps(tmp_path, monkeypatch):
    _, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with gateway:
        for extra in ("verified", "acceptance_path", "audit_path", "image"):
            with pytest.raises(DomainError):
                gateway.submit({**packet, extra: "caller-controlled"})
        with pytest.raises(DomainError):
            gateway.submit({**packet, "execution_mode": "trusted_builtin"})
        budget = {
            **packet["request"]["budget"],
            "memory_bytes": PUBLIC_BUDGET_CAPS.memory_bytes + 1,
        }
        with pytest.raises(DomainError):
            gateway.submit({**packet, "request": {**packet["request"], "budget": budget}})
        assert not gateway.scheduler.store.list()


@pytest.mark.parametrize(
    "name", ["../hostile.json", "sub/hostile.json", "C:/proof", "\\proof", "."]
)
def test_operator_proofs_cannot_escape_receipt_directory(tmp_path, monkeypatch, name):
    _, gateway, _, receipt = setup(tmp_path, monkeypatch)
    receipt["evidence"]["hostile"]["path"] = name
    gateway.config.acceptance_path.write_text(json.dumps(receipt))
    with pytest.raises(DomainError):
        with gateway:
            pass
    assert gateway.scheduler is None


def test_modified_proof_disables_admission_before_staging(tmp_path, monkeypatch):
    _, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with gateway:
        (tmp_path / "hostile.json").write_text("modified after approval")
        assert gateway.capability()["isolated_python"]["available"] is False
        with pytest.raises(DomainError):
            gateway.submit(packet)
        assert not gateway.scheduler.store.list()


def test_queued_job_is_not_hidden_by_newer_cancelled_history(tmp_path, monkeypatch):
    import threading

    _, gateway, packet, _ = setup(tmp_path, monkeypatch)
    start = threading.Event()
    loop = gateway._loop
    monkeypatch.setattr(gateway, "_loop", lambda: (start.wait(), loop()))
    with gateway:
        try:
            oldest = gateway.submit(packet)
            stored = gateway.scheduler.store.get(oldest["id"])
            for _ in range(100):
                job = gateway.scheduler.store._enqueue(stored["kind"], stored["request"])
                gateway.scheduler.cancel(job["id"])
            assert all(job["status"] == "cancelled" for job in gateway.scheduler.store.list())
        finally:
            start.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = gateway.scheduler.store.get(oldest["id"])
            if result["status"] == "succeeded":
                break
            time.sleep(0.05)
        assert result["status"] == "succeeded"


def test_configured_cli_backend_failure_is_json_before_request_read(tmp_path, monkeypatch, capsys):
    root, gateway, _, _ = setup(tmp_path, monkeypatch)
    config = tmp_path / "operator.json"
    config.write_text(
        json.dumps(
            {
                "image": gateway.config.image,
                "seccomp_profile": gateway.config.seccomp_profile,
                "audit_path": str(gateway.config.audit_path),
                "acceptance_path": str(gateway.config.acceptance_path),
            }
        )
    )

    def unavailable():
        raise SandboxUnavailable("isolation_busy")

    monkeypatch.setattr(IsolationGateway, "_owner", lambda self: unavailable())
    assert (
        main(
            [
                "--state-dir",
                str(root),
                "--project-root",
                str(PROJECT),
                "--isolation-config",
                str(config),
                "sandbox-run",
                "--request",
                str(tmp_path / "unread-request.json"),
            ]
        )
        == 1
    )
    error = json.loads(capsys.readouterr().err)
    assert error == {"error": "SandboxUnavailable", "message": "isolation_busy"}
    from quant_terminal.application.jobs import JobStore

    assert not JobStore(root / "isolated-jobs").list()


def test_api_backend_readiness_loss_is_controlled_rejection(tmp_path, monkeypatch):
    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with TestClient(
        create_app(root, PROJECT, isolated_gateway=gateway), base_url="http://127.0.0.1"
    ) as client:

        def unavailable():
            raise SandboxUnavailable("keeper_transport_lost")

        monkeypatch.setattr(gateway.scheduler, "_ready_owner", unavailable)
        response = client.post("/sandbox/strategies", json=packet)
        assert response.status_code == 422
        assert response.json()["message"] == "Isolated gateway unavailable"
        assert not gateway.scheduler.store.list()
