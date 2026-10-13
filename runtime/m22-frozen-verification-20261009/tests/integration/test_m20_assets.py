"""The actual UI examples exercise asset-specific API and shared CLI behavior."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_terminal.advanced_assets.service import research
from quant_terminal.api import create_app
from quant_terminal.cli import main
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta

PROJECT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((PROJECT / "apps/web/src/advancedAssetExamples.json").read_text())


def test_ui_examples_api_units_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    expected = {
        "multi_currency_account": ("base_nav", "1130.8"),
        "fx_conversion": ("target_credit", "90"),
        "borrow_availability": ("approved", False),
        "borrow_fee": ("borrow_fee", "10"),
        "financing": ("debit_interest", "10"),
        "futures_pnl": ("pnl", "1000"),
        "futures_margin": ("margin_call_top_up_to_initial", "5000"),
        "futures_settlement": ("final_cash", "29000"),
        "continuous_contract": ("tradable", False),
        "option_payoff": ("net_pnl", "500"),
        "option_margin": ("required_margin", "1500"),
    }
    with TestClient(create_app(tmp_path / "api", PROJECT), base_url="http://localhost") as client:
        capabilities = client.get("/advanced-assets/capabilities").json()
        assert set(capabilities["operations"]) == set(EXAMPLES)
        for operation, inputs in EXAMPLES.items():
            payload = {"operation": operation, "inputs": inputs}
            response = client.post("/advanced-assets", json=payload)
            assert response.status_code == 200, response.text
            output = response.json()
            assert output["mode"] == "local_asset_research"
            if operation == "option_price":
                assert abs(Decimal(output["result"]["price"]) - Decimal("10.45058357")) < Decimal(
                    ".00002"
                )
            else:
                key, value = expected[operation]
                if isinstance(value, str):
                    assert Decimal(output["result"][key]) == Decimal(value)
                else:
                    assert output["result"][key] == value
            path = tmp_path / "request.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            assert main(["advanced-assets", "--request", str(path)]) == 0
            assert json.loads(capsys.readouterr().out) == output
        assert client.get("/broker/status").json()["order_submission_enabled"] is False
        assert client.post("/advanced-assets", json=[]).status_code == 422
        assert (
            client.post(
                "/advanced-assets", content="{", headers={"content-type": "application/json"}
            ).status_code
            == 422
        )


@pytest.mark.parametrize("value", [True, 0.1, "NaN", "Infinity", "1e1000"])
def test_bad_monetary_json_fails(value: object) -> None:
    with pytest.raises(DomainError):
        research(
            {
                "operation": "borrow_fee",
                "inputs": {
                    "short_quantity": value,
                    "price": "10",
                    "annual_rate": ".1",
                    "days": "1",
                },
            }
        )


@pytest.mark.parametrize(
    "operation,inputs",
    [
        (
            "futures_pnl",
            {"contracts": "1", "multiplier": "50", "entry_price": "10", "exit_price": "11"},
        ),
        ("option_price", {**EXAMPLES["option_price"], "style": "american"}),
        ("option_margin", {**EXAMPLES["option_margin"], "policy": "portfolio_margin"}),
        ("continuous_contract", {**EXAMPLES["continuous_contract"], "method": "ratio"}),
        ("borrow_fee", {**EXAMPLES["borrow_fee"], "broker_credentials": "none"}),
    ],
)
def test_unsupported_asset_modes_fail(operation: str, inputs: dict[str, object]) -> None:
    with pytest.raises(DomainError):
        research({"operation": operation, "inputs": inputs})


@pytest.mark.parametrize("asset_class", ["fx", "future", "option"])
def test_legacy_instrument_rejects_derivative_equity_reuse(asset_class: str) -> None:
    with pytest.raises(DomainError, match="asset_class"):
        InstrumentMeta(
            InstrumentId("TEST"),
            "TEST",
            "USD",
            asset_class,
            "XNYS",
            "America/New_York",
            Decimal(".01"),
            Decimal(1),
        )
