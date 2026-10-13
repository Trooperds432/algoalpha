"""Release observability, bounded load and trusted scheduler recovery."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_terminal.api import create_app
from quant_terminal.application.jobs import JobManager, JobStore, QueueFull
from quant_terminal.release.observability import RuntimeMonitor, storage_health

PROJECT = Path(__file__).resolve().parents[2]


def test_safe_logging_metrics_error_monitor_and_readiness(tmp_path: Path) -> None:
    app = create_app(tmp_path, PROJECT)

    @app.get("/test-error")
    def fail() -> None:
        raise RuntimeError("private-token-do-not-log")

    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
        assert client.get("/ready").status_code == 200
        assert (
            client.post(
                "/broker/connect",
                json={"secret": "body-secret"},
                headers={"authorization": "header-secret"},
            ).status_code
            == 409
        )
        assert client.get("/test-error?token=query-secret").status_code == 500
        metrics = client.get("/metrics").json()
        assert metrics["requests"] >= 3
        assert metrics["status_classes"]["5xx"] == 1
        assert metrics["recent_server_errors"] == [{"category": "http_server_error", "status": 500}]
    logs = (tmp_path / "logs/runtime.jsonl").read_text()
    for value in ("body-secret", "header-secret", "query-secret", "private-token-do-not-log"):
        assert value not in logs
    assert all(json.loads(line)["event"] == "http.request" for line in logs.splitlines())


def test_log_rotation_and_failed_sink_health_latch(tmp_path: Path) -> None:
    monitor = RuntimeMonitor(tmp_path / "logs", max_log_bytes=200)
    for _ in range(300):
        monitor.record(200, 5)
    assert monitor.snapshot()["latency_samples"] == 256
    assert len(list((tmp_path / "logs").iterdir())) == 2
    assert monitor.snapshot()["requests"] == 300
    (tmp_path / "bad").write_text("not a directory")
    bad = RuntimeMonitor(tmp_path / "bad")
    bad.record(500, 2)
    assert bad.healthy is False
    assert bad.snapshot()["log_failures"] == 1


def test_corrupt_database_not_ready(tmp_path: Path) -> None:
    (tmp_path / "jobs.sqlite").write_bytes(b"corrupt database")
    assert storage_health(tmp_path)["healthy"] is False


def test_crash_recovery_preserves_queued_jobs_and_interrupts_claimed_jobs(tmp_path: Path) -> None:
    store = JobStore(tmp_path, capacity=2)
    first = store.submit("backtest", {"strategy": "buy_and_hold"})
    second = store.submit("backtest", {"strategy": "buy_and_hold"})
    assert store.claim()["id"] == first["id"]
    store.recover()
    assert store.get(str(first["id"]))["status"] == "interrupted"
    assert store.get(str(second["id"]))["status"] == "queued"
    store.submit("backtest", {"strategy": "buy_and_hold"})
    with pytest.raises(QueueFull):
        store.submit("backtest", {"strategy": "buy_and_hold"})
    # Restart can acquire/release the OS-owned lease; retained lock files aren't live owners.
    with JobManager(tmp_path / "manager", PROJECT):
        pass
    with JobManager(tmp_path / "manager", PROJECT):
        pass


def test_bounded_concurrent_asgi_load(tmp_path: Path) -> None:
    app = create_app(tmp_path, PROJECT)
    payload = {
        "operation": "fx_conversion",
        "inputs": {
            "source_currency": "USD",
            "target_currency": "EUR",
            "quantity": "100",
            "rate": ".9",
        },
    }
    started = time.monotonic()
    with TestClient(app, base_url="http://localhost") as client:

        def request(_index: int) -> int:
            return client.post("/advanced-assets", json=payload).status_code

        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(request, range(100)))
        assert statuses == [200] * 100
        assert client.get("/metrics").json()["requests"] == 100
        assert client.get("/ready").json()["ready"] is True
    assert time.monotonic() - started < 30


def test_linked_ancestor_rejected_before_log_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = tmp_path / "linked"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or original(path))
    monitor = RuntimeMonitor(linked / "logs")
    monitor.record(200, 1)
    assert monitor.healthy is False
    assert not linked.exists()
    assert storage_health(linked / "state")["reason"] == "unsafe_storage_path"
