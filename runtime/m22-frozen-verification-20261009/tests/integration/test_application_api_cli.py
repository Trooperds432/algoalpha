"""Real API/CLI parity against registered M5/M8 results, without broker activity."""

import json
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from test_application_runtime import setup

from quant_terminal.api import create_app

PROJECT = Path(__file__).parents[2]


def test_cli_and_api_backtests_share_economic_outputs_and_queries(tmp_path):
    runtime, request = setup(tmp_path)
    file = tmp_path / "backtest.json"
    file.write_text(json.dumps(request), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "quant_terminal",
        "--state-dir",
        str(runtime.root),
        "backtest",
        "--request",
        str(file),
    ]
    process = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr + process.stdout
    cli_job = json.loads(process.stdout)
    cli_run = cli_job["result"]
    with TestClient(create_app(runtime.root, PROJECT), base_url="http://127.0.0.1") as client:
        assert client.get("/health").json()["broker_orders_enabled"] is False
        response = client.post("/backtests", json=request)
        assert response.status_code == 202, response.text
        identity = response.json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = client.get(f"/jobs/{identity}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded", job
        api_run = job["result"]
        assert cli_run["result_fingerprint"] == api_run["result_fingerprint"]
        run_id = api_run["run_id"]
        for section in ("metrics", "trades", "orders", "fills", "equity", "artifacts", "report"):
            result = client.get(f"/runs/{run_id}/{section}")
            assert result.status_code == 200, result.text
        assert client.get(f"/runs/{run_id}/metrics").json()["commissions"]["value"] == "1"
        assert client.get(f"/runs/{run_id}/fills").json()["rows"]
        html = client.get(f"/runs/{run_id}/report.html")
        assert html.status_code == 200 and "sandbox" in html.headers["content-security-policy"]
        assert client.get(f"/jobs/{identity}/events").json()
        comparison = client.post("/compare", json={"run_ids": [cli_run["run_id"], run_id]})
        assert comparison.status_code == 200
        assert comparison.json()["warnings"] == []
        assert len(client.get("/strategies").json()) == 3
        assert len(client.get("/datasets").json()) == 1
        assert client.get("/jobs/missing").status_code == 404


def test_api_rejects_unsafe_origins_hosts_floats_and_body_sizes(tmp_path):
    runtime, _ = setup(tmp_path)
    with TestClient(create_app(runtime.root, PROJECT), base_url="http://127.0.0.1") as client:
        assert client.get("/health", headers={"host": "evil.example"}).status_code == 403
        assert client.get("/health", headers={"host": "[broken"}).status_code == 403
        assert client.get("/health", headers={"host": "evil@127.0.0.1"}).status_code == 403
        assert client.get("/health", headers={"origin": "https://evil.example"}).status_code == 403
        assert (
            client.post(
                "/backtests", content="{}", headers={"content-type": "text/plain"}
            ).status_code
            == 415
        )
        assert client.post("/backtests", json={"initial_capital": 1.1}).status_code == 422
        oversized = client.post(
            "/backtests", content=b" " * 10_000_001, headers={"content-type": "application/json"}
        )
        assert oversized.status_code == 413, oversized.text
        assert client.post("/compare", json={"run_ids": []}).status_code == 422
