"""Paper API/CLI parity and default-disabled operational boundaries."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from quant_terminal.api import create_app
from quant_terminal.cli import main
from quant_terminal.paper.clock import LiveClock

PROJECT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 10, 1, 14, 31, tzinfo=UTC)


def settings() -> dict[str, object]:
    return {
        "instrument": {
            "instrument_id": "ETF-1",
            "symbol": "TEST",
            "currency": "USD",
            "asset_class": "etf",
            "calendar": "XNYS",
            "timezone": "America/New_York",
            "tick_size": "0.01",
            "quantity_step": "1",
        },
        "strategy": "buy_and_hold",
        "parameters": {"quantity": "10"},
        "initial_cash": "10000",
        "max_order_notional": "2000",
        "execution": {"commission_per_unit": "0.1"},
        "stale_seconds": 120,
    }


def observation(index: int) -> dict[str, object]:
    end = AT + timedelta(minutes=index)
    return {
        "instrument_id": "ETF-1",
        "frequency": "1m",
        "start": (end - timedelta(minutes=1)).isoformat(),
        "end": end.isoformat(),
        "available_at": end.isoformat(),
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "10000",
        "source": "synthetic-test",
    }


def test_api_restart_reconciles_and_duplicate_fill_never_changes_money(tmp_path: Path) -> None:
    now = [AT]
    app = create_app(tmp_path, PROJECT)
    app.state.paper.clock = LiveClock(lambda: now[0])
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/paper/sessions").json() == []
        assert client.get("/health").json()["broker_orders_enabled"] is False
        response = client.post(
            "/paper/sessions", json={"session_id": "fixture", "config": settings()}
        )
        assert response.status_code == 201
        assert response.json()["connected"] is False
        snap = response.json()["reconciliation"]
        assert (
            client.post(
                "/paper/sessions/fixture/reconnect", json={"event_id": "connect", "snapshot": snap}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/paper/sessions/fixture/events",
                json={"event_id": "one", "sequence": 1, "bar": observation(0)},
            ).status_code
            == 200
        )
        now[0] += timedelta(minutes=1)
        payload = {"event_id": "two", "sequence": 2, "bar": observation(1)}
        response = client.post("/paper/sessions/fixture/events", json=payload)
        assert response.status_code == 200
        economic = response.json()["portfolio"]
        assert economic["cash"] == "8999"
        assert economic["nav"] == "10009"
        assert economic["reconciliation_error"] == "0"
        fill = response.json()["orders"][0]["fills"][0]
        assert (
            client.post("/paper/sessions/fixture/fills", json={"fill": fill}).json()["duplicate"]
            is True
        )
        assert (
            client.post("/paper/sessions/fixture/events", json=payload).json()["portfolio"]
            == economic
        )
    restored = create_app(tmp_path, PROJECT)
    restored.state.paper.clock = LiveClock(lambda: now[0])
    with TestClient(restored, base_url="http://localhost") as client:
        value = client.get("/paper/sessions/fixture").json()
        assert value["portfolio"] == economic
        assert value["connected"] is False
        assert (
            client.post(
                "/paper/sessions/fixture/events",
                json={"event_id": "three", "sequence": 3, "bar": observation(2)},
            ).status_code
            == 422
        )


def test_api_rejects_source_bad_paths_and_unsupported_fields_before_state(tmp_path: Path) -> None:
    app = create_app(tmp_path, PROJECT)
    with TestClient(app, base_url="http://localhost") as client:
        for identity, extra in (("../escape", {}), ("valid", {"source": "raise RuntimeError"})):
            value = {"session_id": identity, "config": {**settings(), **extra}}
            assert client.post("/paper/sessions", json=value).status_code == 422
        config = settings()
        config["strategy"] = "isolated_python"
        assert (
            client.post(
                "/paper/sessions", json={"session_id": "invalid", "config": config}
            ).status_code
            == 422
        )
        assert client.get("/paper/sessions").json() == []
        assert not (tmp_path / "paper").exists()
        assert (
            client.post(
                "/paper/sessions",
                json={"session_id": "bad", "config": settings()},
                headers={"origin": "http://other.test"},
            ).status_code
            == 403
        )


def test_cli_creates_only_disconnected_session_and_status_matches_api(
    tmp_path: Path, capsys
) -> None:
    request = tmp_path / "create.json"
    request.write_text(json.dumps({"session_id": "cli", "config": settings()}))
    args = ["--state-dir", str(tmp_path), "--project-root", str(PROJECT)]
    assert main([*args, "paper-create", "--request", str(request)]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["connected"] is False
    result = subprocess.run(
        [sys.executable, "-m", "quant_terminal", *args, "paper-status", "cli"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    restored = json.loads(result.stdout)
    assert restored["portfolio"] == created["portfolio"]
    assert restored["connected"] is False
    with TestClient(create_app(tmp_path, PROJECT), base_url="http://localhost") as client:
        assert client.get("/paper/sessions/cli").json() == restored
