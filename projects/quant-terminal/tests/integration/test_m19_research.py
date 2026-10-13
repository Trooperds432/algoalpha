"""Local terminal portfolio research uses the same strict seam in API and CLI."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_terminal.api import create_app
from quant_terminal.cli import main
from quant_terminal.core.exceptions import DomainError
from quant_terminal.portfolio_research.service import research

PROJECT = Path(__file__).resolve().parents[2]
REQUEST: dict[str, object] = {
    "operation": "risk_parity",
    "inputs": {"covariance": [["0.04", "0"], ["0", "0.09"]]},
}


def test_shared_cli_and_api(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(REQUEST), encoding="utf-8")
    assert (
        main(["--state-dir", str(tmp_path / "cli"), "portfolio-research", "--request", str(path)])
        == 0
    )
    cli_result = json.loads(capsys.readouterr().out)
    with TestClient(create_app(tmp_path / "api", PROJECT), base_url="http://localhost") as client:
        response = client.post("/portfolio-research", json=REQUEST)
        assert response.status_code == 200
        assert response.json() == cli_result
        weights = [Decimal(value) for value in response.json()["result"]]
        assert abs(weights[0] - Decimal("0.6")) < Decimal("1e-10")
        assert abs(weights[1] - Decimal("0.4")) < Decimal("1e-10")
        assert len(client.get("/portfolio-research/capabilities").json()["operations"]) == 12
        assert client.get("/broker/status").json()["order_submission_enabled"] is False
        assert (
            client.post(
                "/portfolio-research", content="{", headers={"content-type": "application/json"}
            ).status_code
            == 422
        )
        assert client.post("/portfolio-research", json=[]).status_code == 422


@pytest.mark.parametrize("value", [0.04, True, "NaN", "Infinity", "1e1000", "abc"])
def test_financial_json_requires_bounded_decimal_strings(value: object) -> None:
    with pytest.raises(DomainError):
        research({"operation": "concentration", "inputs": {"weights": [value, "1"]}})


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "unknown", "inputs": {}},
        {"operation": "risk_parity", "inputs": {}},
        {"operation": "risk_parity", "inputs": {"covariance": [["1"]], "shorts": True}},
        {"operation": "concentration", "inputs": {"weights": ["1"]}, "trade": True},
    ],
)
def test_unsupported_requests_reject(payload: dict[str, object]) -> None:
    with pytest.raises(DomainError):
        research(payload)


def test_request_precision_is_independent_of_ambient_context() -> None:
    from decimal import localcontext

    with localcontext() as ctx:
        ctx.prec = 7
        a = research(REQUEST)
    with localcontext() as ctx:
        ctx.prec = 50
        b = research(REQUEST)
    assert a == b


@pytest.mark.parametrize(
    ("operation", "inputs", "expected"),
    [
        (
            "volatility_target",
            {"weights": ["1"], "covariance": [["0.04"]], "target": "0.1"},
            (Decimal("0.5"),),
        ),
        ("mean_variance", {"expected_returns": ["0.1"], "covariance": [["0.04"]]}, (Decimal("1"),)),
        (
            "black_litterman",
            {
                "covariance": [["0.04"]],
                "market_weights": ["1"],
                "views": [["1"]],
                "view_returns": ["0.1"],
                "view_covariance": [["0.01"]],
            },
            (Decimal("0.1"),),
        ),
        ("risk_contributions", {"weights": ["1"], "covariance": [["0.04"]]}, (Decimal("0.2"),)),
        (
            "factor_exposures",
            {"weights": ["1"], "loadings": [["2"]]},
            {"exposures": (Decimal("2"),)},
        ),
    ],
)
def test_json_models_use_expected_units(
    operation: str, inputs: dict[str, object], expected: object
) -> None:
    assert research({"operation": operation, "inputs": inputs})["result"] == expected


def test_diagnostic_requests_reach_all_remaining_models() -> None:
    cases: list[tuple[str, dict[str, object], str, object]] = [
        (
            "factor_attribution",
            {
                "weights": ["1"],
                "loadings": [["2"]],
                "factor_returns": ["0.01"],
                "residual_returns": ["0.03"],
            },
            "total_return",
            Decimal("0.05"),
        ),
        (
            "factor_covariance",
            {
                "loadings": [["2"]],
                "factor_covariance_matrix": [["0.04"]],
                "idiosyncratic_variances": ["0.01"],
            },
            "covariance",
            ((Decimal("0.17"),),),
        ),
        (
            "stress_scenarios",
            {"weights": ["1"], "asset_shocks": [["-0.2"]], "nav": "100"},
            "nav_losses",
            (Decimal("20"),),
        ),
        (
            "correlation_regimes",
            {
                "prior_returns": [["0.01", "0.02"], ["0.02", "0.04"]],
                "current_returns": [["0.01", "-0.02"], ["0.02", "-0.04"]],
            },
            "regime",
            "changed",
        ),
        ("concentration", {"weights": ["1"]}, "hhi", Decimal("1")),
        (
            "liquidity_capacity",
            {
                "position_values": ["100"],
                "adv_values": ["1000"],
                "weights": ["1"],
                "participation_limit": "0.1",
            },
            "daily_portfolio_capacity",
            Decimal("100"),
        ),
    ]
    for operation, inputs, key, expected in cases:
        output = research({"operation": operation, "inputs": inputs})["result"]
        assert isinstance(output, dict)
        assert output[key] == expected
