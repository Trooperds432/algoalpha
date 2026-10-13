"""Trusted conversion and economic checks without executing untrusted host code."""

from datetime import datetime
from pathlib import Path

import pytest
from test_event_backtest import END, INSTRUMENT, START, fixture_engine

from quant_terminal.core.exceptions import DomainError
from quant_terminal.sandbox.protocol import (
    ProtocolError,
    SandboxBudget,
    SandboxRequest,
    SandboxResponse,
    WireIntent,
    decode_request,
)
from quant_terminal.sandbox.strategy import IsolatedStrategy

BUDGET = SandboxBudget(5, 3, 64_000_000, 3, 32, 0, 0, 4096)
SOURCE = "def on_bar(context, bar): return []"


class CapturingBackend:
    def __init__(self, intents: tuple[WireIntent, ...] = ()) -> None:
        self.requests: list[SandboxRequest] = []
        self.intents = intents

    def identity(self) -> str:
        return "trusted-test-double"

    def execute(self, request_blob: bytes) -> SandboxResponse:
        request = decode_request(request_blob)
        self.requests.append(request)
        row = dict(request.data_rows[-1])
        proposals = (
            self.intents if row["position"] == "0" and row["has_active_order"] == "False" else ()
        )
        return SandboxResponse(request.job_id, "ok", proposals)


def _strategy(backend: CapturingBackend) -> IsolatedStrategy:
    return IsolatedStrategy(backend, SOURCE, (INSTRUMENT.instrument_id,), BUDGET)


def test_only_as_of_values_cross_the_boundary(tmp_path: Path) -> None:
    backend = CapturingBackend()
    engine, _ = fixture_engine(tmp_path, _strategy(backend))
    result = engine.run(start=START, end=END)
    assert len(backend.requests) == 2
    assert [len(request.data_rows) for request in backend.requests] == [1, 2]
    for request in backend.requests:
        final = dict(request.data_rows[-1])
        as_of = datetime.fromisoformat(final["as_of"])
        assert all(
            datetime.fromisoformat(dict(row)["available_at"]) <= as_of for row in request.data_rows
        )
        assert not any(
            key in {"path", "source", "broker", "token"}
            for row in request.data_rows
            for key, _ in row
        )
    assert not result.orders and not result.trades
    metadata = dict(result.metadata)
    assert metadata["isolated_backend_identity"] == "trusted-test-double"
    assert metadata["isolated_data_slice_count"] == "2"
    assert len(metadata["isolated_source_sha256"]) == 64


def test_valid_proposals_keep_golden_financial_results(tmp_path: Path) -> None:
    from decimal import Decimal

    backend = CapturingBackend((WireIntent("ETF-1", "buy", "market", "10"),))
    engine, _ = fixture_engine(tmp_path, _strategy(backend))
    result = engine.run(start=START, end=END)
    assert [point.nav for point in result.nav] == [
        Decimal("10000"),
        Decimal("10000"),
        Decimal("10001.5"),
    ]
    assert result.commission_cost == Decimal("1.0")
    assert len(result.orders) == 1 and result.trades[0].quantity == 10


def test_proposals_cannot_bypass_engine_risk(tmp_path: Path) -> None:
    backend = CapturingBackend((WireIntent("ETF-1", "buy", "market", "10"),))
    engine, _ = fixture_engine(tmp_path, _strategy(backend), max_notional="500")
    result = engine.run(start=START, end=END)
    assert not result.trades
    assert all(not order.fills for order in result.orders)
    assert all(point.cash == 10000 for point in result.nav)


@pytest.mark.parametrize(
    "intent",
    [
        WireIntent("PRIVATE", "buy", "market", "1"),
        WireIntent("ETF-1", "buy", "market", "0.5"),
        WireIntent("ETF-1", "buy", "market", "NaN"),
    ],
)
def test_invalid_intents_fail_before_order_mutation(tmp_path: Path, intent: WireIntent) -> None:
    backend = CapturingBackend((intent,))
    engine, _ = fixture_engine(tmp_path, _strategy(backend))
    with pytest.raises((DomainError, ProtocolError)):
        engine.run(start=START, end=END)
