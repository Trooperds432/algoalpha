"""The M18 broker panel/status is explicit and has no external-operation seam."""

from pathlib import Path

from fastapi.testclient import TestClient

from quant_terminal.api import create_app

PROJECT = Path(__file__).resolve().parents[2]


def test_broker_status_is_demo_architecture_and_all_external_paths_fail_closed(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path, PROJECT)
    with TestClient(app, base_url="http://localhost") as client:
        status = client.get("/broker/status")
        assert status.status_code == 200
        assert status.json() == {
            "provider": "Trading 212",
            "mode": "demo_architecture_only",
            "connection_enabled": False,
            "order_submission_enabled": False,
            "live_mode_available": False,
            "reason": (
                "M18 architecture only; external connection and orders are disabled by the build"
            ),
        }
        before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
        assert client.post("/broker/connect", json={}).status_code == 409
        assert (
            client.post("/broker/orders", json={"ticker": "AAPL", "quantity": 1}).status_code == 409
        )
        assert (
            client.post("/broker/connect", json={"key": "should_not_be_persisted"}).status_code
            == 409
        )
        after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
        assert after == before
