import json

import pytest
from fastapi.testclient import TestClient
from test_isolated_gateway import PROJECT, setup

from quant_terminal.api import create_app
from quant_terminal.core.exceptions import DomainError


def test_rejected_source_is_audited_without_source(tmp_path, monkeypatch):
    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    packet["source_package"]["dependencies"] = ["SECRET_UNTRUSTED"]
    with gateway:
        with pytest.raises(DomainError):
            gateway.submit(packet)
        lines = gateway.config.audit_path.read_text().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "public_admission_rejected"
        assert event["reason"] == "source_or_request_invalid"
        assert packet["source_package"]["source"] not in lines[0]
        assert "SECRET_UNTRUSTED" not in lines[0]
        assert gateway.scheduler.store.list() == []


def test_admission_audit_failure_prevents_queue_and_latches(tmp_path, monkeypatch):
    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with gateway:
        gateway.config.audit_path.mkdir()
        with pytest.raises(DomainError, match="audit"):
            gateway.submit(packet)
        assert gateway.scheduler.store.list() == []
        assert gateway.capability()["isolated_python"]["available"] is False


def test_cancel_audit_failure_preserves_queued_job(tmp_path, monkeypatch):
    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with gateway:
        monkeypatch.setattr(gateway.scheduler.store, "has_queued", lambda: False)
        job = gateway.submit(packet)
        gateway.config.audit_path.unlink()
        gateway.config.audit_path.mkdir()
        with pytest.raises(DomainError, match="audit"):
            gateway.cancel(job["id"])
        assert gateway.scheduler.store.get(job["id"])["status"] == "queued"
        assert gateway.capability()["isolated_python"]["available"] is False


def test_parser_rejections_are_audited(tmp_path, monkeypatch):
    root, gateway, _, _ = setup(tmp_path, monkeypatch)
    with TestClient(
        create_app(root, PROJECT, isolated_gateway=gateway), base_url="http://127.0.0.1"
    ) as client:
        assert (
            client.post(
                "/sandbox/strategies", content="{", headers={"content-type": "application/json"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/sandbox/strategies",
                content="x" * 2_000_001,
                headers={"content-type": "application/json"},
            ).status_code
            == 413
        )
    events = [json.loads(line) for line in gateway.config.audit_path.read_text().splitlines()]
    assert [e["reason"] for e in events] == ["invalid_json", "request_too_large"]


def test_successful_decisions_bound_metadata_and_noop_cancel(tmp_path, monkeypatch):
    import hashlib
    from datetime import datetime

    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with gateway:
        monkeypatch.setattr(gateway.scheduler.store, "has_queued", lambda: False)
        job = gateway.submit(packet)
        assert gateway.cancel(job["id"])["status"] == "cancelled"
        assert gateway.cancel(job["id"])["status"] == "cancelled"
    lines = gateway.config.audit_path.read_bytes().splitlines()
    events = [json.loads(line) for line in lines]
    assert [e["reason"] for e in events] == ["admit", "cancel", "unchanged"]
    for line, event in zip(lines, events, strict=True):
        assert len(line) < 4096
        assert datetime.fromisoformat(event["timestamp"]).utcoffset().total_seconds() == 0
        assert event["job_id"] == job["id"]
        assert event["backend_version"] == "fixture"
        assert event["budget"] == packet["request"]["budget"]
        assert (
            event["source_sha256"]
            == hashlib.sha256(packet["source_package"]["source"].encode()).hexdigest()
        )
        assert event["phase"] == "decision_before_commit"
        assert packet["source_package"]["source"].encode() not in line


@pytest.mark.parametrize(
    "case,reason",
    [
        ("shape", "invalid_packet"),
        ("mode", "invalid_execution_mode"),
        ("budget", "budget_rejected"),
        ("hash", "source_or_request_invalid"),
        ("readiness", "gateway_unavailable"),
        ("queue", "queue_full"),
    ],
)
def test_rejection_classes(tmp_path, monkeypatch, case, reason):
    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with gateway:
        monkeypatch.setattr(gateway.scheduler.store, "has_queued", lambda: False)
        if case == "shape":
            packet = []
        elif case == "mode":
            packet["execution_mode"] = "trusted_builtin"
        elif case == "budget":
            packet["request"]["budget"]["wall_seconds"] = 6
        elif case == "hash":
            packet["source_package"]["source_sha256"] = "0" * 64
        elif case == "readiness":
            gateway.failure = "fixture_unavailable"
        elif case == "queue":
            gateway.scheduler.store.capacity = 1
            gateway.submit(packet)
        with pytest.raises(DomainError):
            gateway.submit(packet)
        event = json.loads(gateway.config.audit_path.read_text().splitlines()[-1])
        assert event["event"] == "public_admission_rejected"
        assert event["reason"] == reason


@pytest.mark.parametrize(
    "case,reason,status",
    [
        ("host", "invalid_host", 403),
        ("origin", "invalid_origin", 403),
        ("media", "invalid_content_type", 415),
        ("large", "request_too_large", 413),
    ],
)
def test_configured_transport_rejections(tmp_path, monkeypatch, case, reason, status):
    root, gateway, _, _ = setup(tmp_path, monkeypatch)
    with TestClient(
        create_app(root, PROJECT, isolated_gateway=gateway), base_url="http://127.0.0.1"
    ) as client:
        headers = {"content-type": "application/json"}
        body = "{}"
        if case == "host":
            headers["host"] = "evil.example"
        elif case == "origin":
            headers["origin"] = "https://evil.example"
        elif case == "media":
            headers["content-type"] = "text/plain"
        else:
            body = "x" * 10_000_001
        assert (
            client.post("/sandbox/strategies", content=body, headers=headers).status_code == status
        )
    events = [json.loads(line) for line in gateway.config.audit_path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["reason"] == reason


def test_audit_fsync_failure_rolls_back_cancel_and_keeps_owner(tmp_path, monkeypatch):
    import quant_terminal.sandbox.public_audit as module

    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with gateway:
        monkeypatch.setattr(gateway.scheduler.store, "has_queued", lambda: False)
        job = gateway.submit(packet)
        owner = gateway.scheduler._owner

        def fail(_):
            raise OSError("fixture fsync failed")

        monkeypatch.setattr(module.os, "fsync", fail)
        with pytest.raises(DomainError, match="audit"):
            gateway.cancel(job["id"])
        assert gateway.scheduler.store.get(job["id"])["status"] == "queued"
        assert gateway.scheduler._owner is owner and owner.ready
        assert gateway.scheduler.lease.handle is not None


def test_decision_callback_failure_cannot_be_claimed(tmp_path):
    from quant_terminal.application.jobs import JobStore

    store = JobStore(tmp_path)

    def fail(_):
        raise DomainError("required decision failed")

    with pytest.raises(DomainError):
        store._enqueue("isolated-backtest", {}, decision=fail)
    assert store.claim() is None
    assert store.list() == []


def test_http_cancel_and_missing_identity_are_audited(tmp_path, monkeypatch):
    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with TestClient(
        create_app(root, PROJECT, isolated_gateway=gateway), base_url="http://127.0.0.1"
    ) as client:
        monkeypatch.setattr(gateway.scheduler.store, "has_queued", lambda: False)
        job = client.post("/sandbox/strategies", json=packet).json()
        assert (
            client.post("/sandbox/jobs/" + job["id"] + "/cancel", json={}).json()["status"]
            == "cancelled"
        )
        assert client.post("/sandbox/jobs/SECRET_FAKE_JOB/cancel", json={}).status_code == 404
    lines = gateway.config.audit_path.read_text().splitlines()
    events = [json.loads(line) for line in lines]
    assert [e["event"] for e in events] == [
        "public_admission_decision",
        "public_cancel_decision",
        "public_cancel_rejected",
    ]
    assert "SECRET_FAKE_JOB" not in lines[-1]
    assert "job_id" not in events[-1]


def test_short_audit_write_prevents_admission(tmp_path, monkeypatch):
    from pathlib import Path

    root, gateway, packet, _ = setup(tmp_path, monkeypatch)
    with gateway:
        original = Path.open

        class ShortWrite:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def write(self, raw):
                return len(raw) - 1

        def open_path(path, *args, **kwargs):
            if path == gateway.config.audit_path:
                return ShortWrite()
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_path)
        with pytest.raises(DomainError, match="audit"):
            gateway.submit(packet)
        assert gateway.scheduler.store.list() == []
        assert gateway.scheduler.lease.handle is not None


def test_ingress_audit_failure_never_acknowledges_original_rejection(tmp_path, monkeypatch):
    root, gateway, _, _ = setup(tmp_path, monkeypatch)
    with TestClient(
        create_app(root, PROJECT, isolated_gateway=gateway), base_url="http://127.0.0.1"
    ) as client:
        gateway.config.audit_path.mkdir()
        assert (
            client.post(
                "/sandbox/strategies", content="{}", headers={"content-type": "text/plain"}
            ).status_code
            == 503
        )
        assert gateway.capability()["isolated_python"]["available"] is False
        assert gateway.scheduler.store.list() == []


def test_claim_waits_for_admission_decision_transaction(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from quant_terminal.application.jobs import JobStore

    store = JobStore(tmp_path)
    entered, release, claim_started = threading.Event(), threading.Event(), threading.Event()

    def decision(_):
        entered.set()
        assert release.wait(3)
        raise DomainError("required audit failed")

    def enqueue():
        with pytest.raises(DomainError):
            store._enqueue("isolated-backtest", {}, decision=decision)

    def claim():
        claim_started.set()
        return store.claim()

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(enqueue)
        assert entered.wait(3)
        claimed = pool.submit(claim)
        assert claim_started.wait(3)
        assert not claimed.done()
        release.set()
        pending.result(timeout=3)
        assert claimed.result(timeout=3) is None


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("missing", "request_file_required"),
        ("unreadable", "request_file_unavailable"),
        ("oversized", "request_too_large"),
        ("malformed", "invalid_json"),
    ],
)
def test_configured_cli_ingress_rejections_are_audited(
    tmp_path, monkeypatch, capsys, failure, reason
):
    import quant_terminal.sandbox.gateway as gateway_module
    from quant_terminal.cli import main

    root, gateway, _, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(gateway_module, "IsolationGateway", lambda *_: gateway)
    monkeypatch.setattr(gateway_module.OperatorIsolationConfig, "load", lambda _: gateway.config)
    args = [
        "--state-dir",
        str(root),
        "--project-root",
        str(PROJECT),
        "--isolation-config",
        "operator.json",
        "sandbox-run",
    ]
    if failure != "missing":
        request = tmp_path / "SECRET_REQUEST_FILENAME.json"
        if failure == "oversized":
            request.write_bytes(b"x" * 2_000_001)
        elif failure == "malformed":
            request.write_text("{")
        args += ["--request", str(request)]
    assert main(args) == 1
    assert gateway.scheduler.store.list() == []
    lines = gateway.config.audit_path.read_text().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "public_admission_rejected"
    assert event["reason"] == reason
    assert "SECRET_REQUEST_FILENAME" not in lines[0]
    capsys.readouterr()


def test_cli_parser_audit_failure_is_unavailable_not_parse_ack(tmp_path, monkeypatch, capsys):
    import quant_terminal.sandbox.gateway as gateway_module
    from quant_terminal.cli import main

    root, gateway, _, _ = setup(tmp_path, monkeypatch)
    gateway.config.audit_path.mkdir()
    monkeypatch.setattr(gateway_module, "IsolationGateway", lambda *_: gateway)
    monkeypatch.setattr(gateway_module.OperatorIsolationConfig, "load", lambda _: gateway.config)
    request = tmp_path / "SECRET_REQUEST_FILENAME.json"
    request.write_text("{")
    assert (
        main(
            [
                "--state-dir",
                str(root),
                "--project-root",
                str(PROJECT),
                "--isolation-config",
                "operator.json",
                "sandbox-run",
                "--request",
                str(request),
            ]
        )
        == 1
    )
    error = json.loads(capsys.readouterr().err)
    assert error["message"] == "Required public security audit unavailable"
    assert gateway.failure == "public_audit_unavailable"
    assert gateway.scheduler.store.list() == []
    assert "SECRET_REQUEST_FILENAME" not in json.dumps(error)
