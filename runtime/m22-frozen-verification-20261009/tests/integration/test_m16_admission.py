"""Public entry points must reject arbitrary source before creating work."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_terminal.api import create_app

PROJECT = Path(__file__).parents[2]


def test_api_reports_unavailable_and_rejects_before_submission(tmp_path: Path) -> None:
    state = tmp_path / "research"
    with TestClient(create_app(state, PROJECT), base_url="http://127.0.0.1") as client:
        before = client.get("/jobs").json()
        capability = client.get("/sandbox/capabilities")
        assert capability.status_code == 200
        assert capability.json()["isolated_python"] == {
            "available": False,
            "reason": "isolation_backend_unverified",
        }
        response = client.post("/sandbox/strategies", json={"source": "raise Exception()"})
        assert response.status_code == 422
        assert "isolation_backend_unverified" in response.json()["message"]
        assert client.get("/jobs").json() == before
        assert not list(state.rglob("*strategy*"))


def test_cli_rejects_without_reading_request_or_creating_state(tmp_path: Path) -> None:
    state = tmp_path / "research"
    command = [
        sys.executable,
        "-m",
        "quant_terminal",
        "--state-dir",
        str(state),
        "sandbox-run",
    ]
    result = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(result.stderr)["message"].endswith("isolation_backend_unverified")
    assert not state.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"strategy": "isolated_python"},
        {"strategy": {"name": "buy_and_hold"}},
        {"strategy": "buy_and_hold", "source": "raise Exception()"},
        {"base_request": {"strategy": "buy_and_hold", "source_path": "../private.py"}},
        {"strategy": "buy_and_hold", "execution_mode": "trusted_builtin"},
    ],
)
def test_trusted_api_job_rejects_source_mode_confusion_before_queue(
    tmp_path: Path, payload: dict
) -> None:
    with TestClient(create_app(tmp_path / "state", PROJECT), base_url="http://127.0.0.1") as client:
        response = client.post("/backtests", json=payload)
        assert response.status_code == 422
        assert client.get("/jobs").json() == []
