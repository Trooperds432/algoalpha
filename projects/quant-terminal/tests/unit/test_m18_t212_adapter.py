from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId, OrderId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.execution.brokers.trading212 import (
    DEMO_BASE_URL,
    ENDPOINT_BUDGETS,
    OPENAPI_RATE_LIMIT_EVIDENCE,
    OPENAPI_SCHEMA_REVIEWED_DATE,
    OPENAPI_SCHEMA_SOURCE,
    DemoCredentials,
    ProviderOrderFillAggregate,
    ProviderSchemaError,
    SnapshotFreshnessState,
    Trading212Adapter,
    assess_snapshot_freshness,
    compare_local_broker_state,
    parse_rate_limit_headers,
    parse_retry_after,
)
from quant_terminal.orders.models import OrderIntent, Side, TradeType


@pytest.fixture
def instrument() -> InstrumentMeta:
    return InstrumentMeta(
        instrument_id=InstrumentId("spy"),
        symbol="SPY",
        currency="USD",
        asset_class="etf",
        calendar="XNYS",
        timezone="America/New_York",
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )


@pytest.fixture
def adapter(instrument: InstrumentMeta) -> Trading212Adapter:
    return Trading212Adapter(
        DemoCredentials("synthetic-key", "synthetic-secret"),
        {"SPY_US_EQ": instrument},
        "USD",
    )


def _intent(instrument: InstrumentMeta, side: Side = Side.BUY) -> OrderIntent:
    return OrderIntent(
        order_id=OrderId("local-1"),
        instrument=instrument,
        side=side,
        order_type=TradeType.LIMIT,
        quantity=Decimal("2.5"),
        limit_price=Decimal("501.25"),
        created_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
    )


def _provider_order(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 1234,
        "ticker": "SPY_US_EQ",
        "currency": "USD",
        "side": "BUY",
        "status": "PARTIALLY_FILLED",
        "quantity": 2.5,
        "filledQuantity": 0.5,
        "createdAt": "2026-10-01T12:00:00Z",
        "type": "LIMIT",
        "limitPrice": 501.25,
    }
    row.update(overrides)
    return row


def _account_summary(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 123456,
        "currency": "USD",
        "cash": {
            "availableToTrade": 1000.25,
            "inPies": 50,
            "reservedForOrders": 25,
        },
        "investments": {
            "currentValue": 1250.5,
            "realizedProfitLoss": -10.5,
            "totalCost": 1200,
            "unrealizedProfitLoss": 50.5,
        },
        "totalValue": 2300.75,
    }
    row.update(overrides)
    return row


def _provider_position(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "averagePricePaid": 495.123,
        "createdAt": "2026-09-01T12:00:00Z",
        "currentPrice": 501.25,
        "instrument": {
            "currency": "USD",
            "isin": "US78462F1030",
            "name": "SPDR S&P 500 ETF Trust",
            "ticker": "SPY_US_EQ",
        },
        "quantity": 2.5,
        "quantityAvailableForTrading": 2,
        "quantityInPies": 0.5,
        "walletImpact": {
            "currency": "USD",
            "currentValue": 1253.125,
            "fxImpact": None,
            "totalCost": 1237.8075,
            "unrealizedProfitLoss": 15.3175,
        },
    }
    row.update(overrides)
    return row


def _history_row(
    *,
    order_id: int = 1234,
    fill_id: int = 5678,
    fill_quantity: float = 0.5,
    fill_price: float = 501.25,
    filled_at: str = "2026-10-01T12:01:00Z",
):
    order = _provider_order(id=order_id)
    return {
        "order": order,
        "fill": {
            "id": fill_id,
            "filledAt": filled_at,
            "price": fill_price,
            "quantity": fill_quantity,
            "type": "TRADE",
        },
    }


def test_auth_is_basic_ephemeral_and_repr_redacted() -> None:
    credentials = DemoCredentials("synthetic-key", "synthetic-secret")
    assert credentials.basic_authorization() == "Basic " + base64.b64encode(
        b"synthetic-key:synthetic-secret"
    ).decode("ascii")
    assert "synthetic-secret" not in repr(credentials)
    request = Trading212Adapter(
        credentials, {"SPY_US_EQ": _instrument()}, "USD"
    ).describe_positions_request()
    assert request.url.startswith(DEMO_BASE_URL)
    assert "live" not in request.url.lower()
    assert "synthetic-secret" not in repr(request)
    assert "<redacted>" in repr(request)
    assert request.url == f"{DEMO_BASE_URL}/equity/positions"


def _instrument() -> InstrumentMeta:
    return InstrumentMeta(
        instrument_id=InstrumentId("spy"),
        symbol="SPY",
        currency="USD",
        asset_class="etf",
        calendar="XNYS",
        timezone="America/New_York",
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )


def test_limit_request_uses_trusted_ticker_and_negative_sell_quantity(adapter, instrument) -> None:
    buy = adapter.describe_limit_order_request(_intent(instrument))
    sell = adapter.describe_limit_order_request(_intent(instrument, Side.SELL))
    assert buy.method == sell.method == "POST"
    assert buy.url == f"{DEMO_BASE_URL}/equity/orders/limit"
    assert dict(buy.body or ()) == {
        "ticker": "SPY_US_EQ",
        "quantity": "2.5",
        "limitPrice": "501.25",
        "timeValidity": "DAY",
    }
    assert dict(sell.body or ())["quantity"] == "-2.5"
    assert "POST /equity/orders/limit" in ENDPOINT_BUDGETS


def test_endpoint_budgets_match_reviewed_openapi_rates() -> None:
    assert OPENAPI_SCHEMA_REVIEWED_DATE == "2026-10-02"
    assert OPENAPI_SCHEMA_SOURCE.endswith("api.json?download=")
    assert ENDPOINT_BUDGETS["GET /equity/account/summary"].period == timedelta(seconds=5)
    assert ENDPOINT_BUDGETS["GET /equity/positions"].period == timedelta(seconds=1)
    assert ENDPOINT_BUDGETS["GET /equity/orders"].period == timedelta(seconds=5)
    assert ENDPOINT_BUDGETS["GET /equity/history/orders"].requests == 20
    assert ENDPOINT_BUDGETS["GET /equity/history/orders"].period == timedelta(minutes=1)
    assert ENDPOINT_BUDGETS["POST /equity/orders/limit"].period == timedelta(seconds=2)
    assert "GET /equity/portfolio" not in ENDPOINT_BUDGETS
    assert OPENAPI_RATE_LIMIT_EVIDENCE == (
        ("GET", "/api/v0/equity/account/summary", 1, 5),
        ("GET", "/api/v0/equity/positions", 1, 1),
        ("GET", "/api/v0/equity/orders", 1, 5),
        ("GET", "/api/v0/equity/history/orders", 20, 60),
        ("POST", "/api/v0/equity/orders/limit", 1, 2),
    )


@pytest.mark.parametrize("order_type", [TradeType.MARKET, TradeType.STOP, TradeType.STOP_LIMIT])
def test_unsupported_order_types_refuse_before_a_request_description(
    adapter, instrument, order_type
) -> None:
    with pytest.raises(DomainError):
        adapter.describe_limit_order_request(
            OrderIntent(
                order_id=OrderId("local-2"),
                instrument=instrument,
                side=Side.BUY,
                order_type=order_type,
                quantity=Decimal("1"),
                limit_price=Decimal("500") if order_type is TradeType.STOP_LIMIT else None,
                stop_price=Decimal("490")
                if order_type in (TradeType.STOP, TradeType.STOP_LIMIT)
                else None,
                created_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
            )
        )


def test_limit_rejects_non_account_currency(adapter, instrument) -> None:
    eur = InstrumentMeta(
        instrument_id=InstrumentId("spy-eur"),
        symbol="SPY",
        currency="EUR",
        asset_class="etf",
        calendar="XNYS",
        timezone="America/New_York",
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )
    adapter_eur = Trading212Adapter(DemoCredentials("key", "secret"), {"SPY_EU_EQ": eur}, "USD")
    with pytest.raises(DomainError, match="currency"):
        adapter_eur.describe_limit_order_request(_intent(eur))


def test_account_summary_parser_maps_exact_values(adapter) -> None:
    parsed = adapter.parse_account_summary(_account_summary())
    assert parsed.account_id == 123456
    assert parsed.currency == "USD"
    assert parsed.available_to_trade == Decimal("1000.25")
    assert parsed.cash_in_pies == Decimal("50")
    assert parsed.reserved_for_orders == Decimal("25")
    assert parsed.investment_realized_profit_loss == Decimal("-10.5")
    assert parsed.total_value == Decimal("2300.75")


@pytest.mark.parametrize(
    "payload",
    [
        {**_account_summary(), "currency": "EUR"},
        {**_account_summary(), "unexpected": 1},
        {key: value for key, value in _account_summary().items() if key != "totalValue"},
        {**_account_summary(), "cash": {"availableToTrade": 1, "inPies": 0}},
        {**_account_summary(), "id": True},
        {**_account_summary(), "totalValue": float("nan")},
    ],
)
def test_account_summary_parser_rejects_missing_extra_currency_and_bad_values(
    adapter, payload
) -> None:
    with pytest.raises(ProviderSchemaError):
        adapter.parse_account_summary(payload)


def test_portfolio_parser_maps_trusted_position_and_empty_snapshot(adapter) -> None:
    parsed = adapter.parse_portfolio([_provider_position()])
    assert parsed.positions[0].ticker == "SPY_US_EQ"
    assert parsed.positions[0].quantity == Decimal("2.5")
    assert parsed.positions[0].quantity_available == Decimal("2")
    assert parsed.positions[0].quantity_in_pies == Decimal("0.5")
    assert parsed.positions[0].current_value == Decimal("1253.125")
    assert parsed.positions[0].fx_impact is None
    assert adapter.parse_portfolio([]).positions == ()


@pytest.mark.parametrize(
    "position",
    [
        {**_provider_position(), "unexpected": 1},
        {key: value for key, value in _provider_position().items() if key != "quantity"},
        {**_provider_position(), "quantity": 0},
        {**_provider_position(), "quantity": 2.5005},
        {**_provider_position(), "quantityAvailableForTrading": 3},
        {**_provider_position(), "quantityInPies": 1},
        {
            **_provider_position(),
            "instrument": {**_provider_position()["instrument"], "currency": "EUR"},
        },
        {
            **_provider_position(),
            "walletImpact": {**_provider_position()["walletImpact"], "currency": "EUR"},
        },
        {
            **_provider_position(),
            "instrument": {**_provider_position()["instrument"], "ticker": "NOPE_US_EQ"},
        },
        {**_provider_position(), "createdAt": "2026-09-01T12:00:00"},
    ],
)
def test_portfolio_parser_rejects_schema_currency_and_quantity_errors(adapter, position) -> None:
    with pytest.raises(ProviderSchemaError):
        adapter.parse_portfolio([position])


def test_portfolio_parser_rejects_duplicate_tickers_and_non_array_payload(adapter) -> None:
    with pytest.raises(ProviderSchemaError, match="duplicate ticker"):
        adapter.parse_portfolio([_provider_position(), _provider_position()])
    with pytest.raises(ProviderSchemaError):
        adapter.parse_portfolio({"items": [_provider_position()]})


@pytest.mark.parametrize(
    "position",
    [
        {**_provider_position(), "quantity": "1e1000000"},
        {**_provider_position(), "quantity": "1e-1000000"},
    ],
)
def test_portfolio_parser_bounds_extreme_decimal_exponents(adapter, position) -> None:
    with pytest.raises(ProviderSchemaError, match="quantities violate bounds or step"):
        adapter.parse_portfolio([position])


def test_portfolio_parser_bounds_extreme_configured_decimal_step(instrument) -> None:
    extreme_instrument = InstrumentMeta(
        instrument_id=InstrumentId("spy-extreme-step"),
        symbol="SPY",
        currency="USD",
        asset_class="etf",
        calendar="XNYS",
        timezone="America/New_York",
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("1e-1000000"),
    )
    extreme_adapter = Trading212Adapter(
        DemoCredentials("test-key", "test-secret"),
        {"SPY_US_EQ": extreme_instrument},
        "USD",
    )
    with pytest.raises(ProviderSchemaError, match="quantities violate bounds or step"):
        extreme_adapter.parse_portfolio([_provider_position()])


def test_snapshot_freshness_blocks_stale_future_and_naive_observations() -> None:
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    fresh = assess_snapshot_freshness(
        now - timedelta(seconds=5), now=now, max_age=timedelta(seconds=10)
    )
    assert fresh.state is SnapshotFreshnessState.FRESH and not fresh.blocked
    stale = assess_snapshot_freshness(
        now - timedelta(seconds=11), now=now, max_age=timedelta(seconds=10)
    )
    assert stale.state is SnapshotFreshnessState.STALE and stale.blocked
    future = assess_snapshot_freshness(
        now + timedelta(seconds=1), now=now, max_age=timedelta(seconds=10)
    )
    assert future.state is SnapshotFreshnessState.FUTURE and future.blocked
    with pytest.raises(DomainError, match="timezone-aware"):
        assess_snapshot_freshness(now.replace(tzinfo=None), now=now, max_age=timedelta(seconds=10))


def test_reconciliation_exact_match_is_immutable_and_uses_ticker_union(adapter) -> None:
    account = adapter.parse_account_summary(_account_summary())
    portfolio = adapter.parse_portfolio([_provider_position()])
    local_positions = {"SPY_US_EQ": Decimal("2.500")}
    result = compare_local_broker_state(
        account,
        portfolio,
        local_currency="USD",
        local_available_cash=Decimal("1000.25"),
        local_positions=local_positions,
    )
    assert result.matches and result.cash_matches
    assert result.positions[0].ticker == "SPY_US_EQ"
    assert result.positions[0].matches
    assert local_positions == {"SPY_US_EQ": Decimal("2.500")}
    assert portfolio.positions[0].quantity == Decimal("2.5")
    assert result.broker_snapshot_id.startswith("sha256:")


def test_broker_snapshot_id_is_stable_and_changes_with_broker_content(adapter) -> None:
    account = adapter.parse_account_summary(_account_summary())
    portfolio = adapter.parse_portfolio([_provider_position()])
    local_positions = {"SPY_US_EQ": Decimal("2.5")}
    first = compare_local_broker_state(
        account,
        portfolio,
        local_currency="USD",
        local_available_cash=Decimal("1000.25"),
        local_positions=local_positions,
    )
    repeated = compare_local_broker_state(
        account,
        portfolio,
        local_currency="USD",
        local_available_cash=Decimal("1000.25"),
        local_positions=local_positions,
    )
    changed_account = adapter.parse_account_summary(_account_summary(totalValue=2301.0))
    changed = compare_local_broker_state(
        changed_account,
        portfolio,
        local_currency="USD",
        local_available_cash=Decimal("1000.25"),
        local_positions=local_positions,
    )
    assert first.broker_snapshot_id == repeated.broker_snapshot_id
    assert first.broker_snapshot_id != changed.broker_snapshot_id


def test_reconciliation_reports_exact_cash_and_position_deltas(adapter) -> None:
    account = adapter.parse_account_summary(_account_summary())
    portfolio = adapter.parse_portfolio([_provider_position()])
    result = compare_local_broker_state(
        account,
        portfolio,
        local_currency="USD",
        local_available_cash=Decimal("1000.24"),
        local_positions={"SPY_US_EQ": Decimal("2.4")},
    )
    assert not result.matches and not result.cash_matches
    position = result.positions[0]
    assert position.local_quantity == Decimal("2.4")
    assert position.broker_quantity == Decimal("2.5")
    assert not position.matches


def test_reconciliation_union_reports_missing_and_additional_positions(adapter) -> None:
    account = adapter.parse_account_summary(_account_summary())
    portfolio = adapter.parse_portfolio([_provider_position()])
    missing_local = compare_local_broker_state(
        account,
        portfolio,
        local_currency="USD",
        local_available_cash=Decimal("1000.25"),
        local_positions={},
    )
    assert not missing_local.matches
    assert missing_local.positions[0].local_quantity == 0
    additional_local = compare_local_broker_state(
        account,
        adapter.parse_portfolio([]),
        local_currency="USD",
        local_available_cash=Decimal("1000.25"),
        local_positions={"EXTRA_US_EQ": Decimal("1")},
    )
    assert not additional_local.matches
    assert additional_local.positions[0].broker_quantity == 0


def test_reconciliation_rejects_currency_and_unparsed_schema_mismatch(adapter) -> None:
    account = adapter.parse_account_summary(_account_summary())
    portfolio = adapter.parse_portfolio([_provider_position()])
    with pytest.raises(ProviderSchemaError, match="currencies differ"):
        compare_local_broker_state(
            account,
            portfolio,
            local_currency="EUR",
            local_available_cash=Decimal("1000.25"),
            local_positions={"SPY_US_EQ": Decimal("2.5")},
        )
    with pytest.raises(ProviderSchemaError):
        adapter.parse_account_summary({**_account_summary(), "newField": "schema drift"})
    with pytest.raises(DomainError, match="snapshots are required"):
        compare_local_broker_state(
            {},
            portfolio,
            local_currency="USD",
            local_available_cash=Decimal("1000.25"),
            local_positions={},
        )


def test_order_parser_maps_status_and_keeps_transitional_states_blocked(adapter) -> None:
    mapped = adapter.parse_order(_provider_order())
    assert mapped.broker_order_id == "1234"
    assert mapped.order_id.value == "t212:1234"
    assert mapped.status.internal.value == "partially_filled"
    assert mapped.status.blocked is False
    cancelling = adapter.parse_order(_provider_order(status="CANCELLING"))
    assert cancelling.status.blocked is True
    replacing = adapter.parse_order(_provider_order(status="REPLACING"))
    assert replacing.status.blocked is True
    replaced = adapter.parse_order(_provider_order(status="REPLACED"))
    assert replaced.status.internal is None and replaced.status.blocked


def test_sell_order_rows_use_negative_provider_quantity_but_positive_domain_magnitude(
    adapter,
) -> None:
    mapped = adapter.parse_order(_provider_order(side="SELL", quantity=-2.5, filledQuantity=-0.5))
    assert mapped.side is Side.SELL
    assert mapped.quantity == Decimal("2.5")
    assert mapped.filled_quantity == Decimal("0.5")
    with pytest.raises(ProviderSchemaError):
        adapter.parse_order(_provider_order(side="SELL", quantity=2.5))


def test_individual_historical_fill_keeps_provider_identity_and_facts(adapter) -> None:
    order, fill = adapter.parse_historical_order(
        {
            "order": _provider_order(),
            "fill": {
                "id": 5678,
                "filledAt": "2026-10-01T12:01:00Z",
                "price": 500.25,
                "quantity": 0.5,
                "type": "TRADE",
            },
        }
    )
    assert order.broker_order_id == fill.broker_order_id == "1234"
    assert fill.broker_fill_id == "5678"
    assert fill.quantity == Decimal("0.5")
    assert fill.price == Decimal("500.25")


def test_historical_order_without_real_fill_identity_does_not_infer_fill(adapter) -> None:
    with pytest.raises(ProviderSchemaError, match="no fill is inferred"):
        adapter.parse_historical_order({"order": _provider_order(), "fill": None})


def test_historical_order_envelope_rejects_unreviewed_fields(adapter) -> None:
    with pytest.raises(ProviderSchemaError, match="historical order envelope"):
        adapter.parse_historical_order(
            {"order": _provider_order(), "fill": {}, "extra": "unexpected"}
        )


@pytest.mark.parametrize(
    ("order", "fill_quantity"),
    [
        (_provider_order(side="SELL", quantity=-2.5, filledQuantity=-0.5), 0.5),
        (_provider_order(), -0.5),
        (_provider_order(), 0.75),
        (_provider_order(status="NEW", filledQuantity=0), 0.1),
    ],
)
def test_historical_fill_sign_status_and_aggregate_quantity_must_agree(
    adapter, order, fill_quantity
) -> None:
    with pytest.raises(ProviderSchemaError):
        adapter.parse_historical_order(
            {
                "order": order,
                "fill": {
                    "id": 6789,
                    "filledAt": "2026-10-01T12:01:00Z",
                    "price": 500.25,
                    "quantity": fill_quantity,
                    "type": "TRADE",
                },
            }
        )


def test_provider_order_fill_aggregate_binds_local_intent_and_exact_history(adapter, instrument):
    aggregate = adapter.aggregate_historical_order_fills(
        [
            _history_row(fill_id=5678, fill_quantity=0.2),
            _history_row(fill_id=5679, fill_quantity=0.3),
        ],
        expected_broker_order_id="1234",
        expected_local_order=_intent(instrument),
    )
    assert aggregate.valid and aggregate.verify_hash()
    assert aggregate.local_order_id == OrderId("local-1")
    assert aggregate.mapped_order.order_id == OrderId("t212:1234")
    assert aggregate.broker_order_id == "1234"
    assert aggregate.ticker == "SPY_US_EQ"
    assert aggregate.side is Side.BUY
    assert aggregate.status.value == "PARTIALLY_FILLED"
    assert aggregate.order_quantity == Decimal("2.5")
    assert aggregate.filled_quantity == Decimal("0.5")
    assert aggregate.fill_ids == ("5678", "5679")
    assert tuple(fill.quantity for fill in aggregate.fills) == (Decimal("0.2"), Decimal("0.3"))


def test_provider_order_fill_aggregate_rejects_duplicate_and_mixed_order_records(
    adapter, instrument
):
    with pytest.raises(ProviderSchemaError, match="duplicate fill ID"):
        adapter.aggregate_historical_order_fills(
            [
                _history_row(fill_id=5678, fill_quantity=0.2),
                _history_row(fill_id=5678, fill_quantity=0.3),
            ],
            expected_broker_order_id="1234",
            expected_local_order=_intent(instrument),
        )
    with pytest.raises(ProviderSchemaError, match="expected broker order ID"):
        adapter.aggregate_historical_order_fills(
            [_history_row(order_id=9876)],
            expected_broker_order_id="1234",
            expected_local_order=_intent(instrument),
        )
    mixed_rows = [
        _history_row(fill_quantity=0.2),
        _history_row(order_id=9876, fill_id=5679, fill_quantity=0.3),
    ]
    with pytest.raises(ProviderSchemaError, match="disagree on broker order identity"):
        adapter.aggregate_historical_order_fills(
            mixed_rows,
            expected_broker_order_id="1234",
            expected_local_order=_intent(instrument),
        )


def test_provider_order_fill_aggregate_rejects_wrong_local_intent_and_incomplete_history(
    adapter, instrument
):
    wrong_local = OrderIntent(
        order_id=OrderId("wrong-local"),
        instrument=instrument,
        side=Side.BUY,
        order_type=TradeType.LIMIT,
        quantity=Decimal("3"),
        limit_price=Decimal("501.25"),
        created_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
    )
    with pytest.raises(ProviderSchemaError, match="does not match the expected local"):
        adapter.aggregate_historical_order_fills(
            [_history_row()],
            expected_broker_order_id="1234",
            expected_local_order=wrong_local,
        )
    with pytest.raises(ProviderSchemaError, match="fill sum differs"):
        adapter.aggregate_historical_order_fills(
            [_history_row(fill_quantity=0.2)],
            expected_broker_order_id="1234",
            expected_local_order=_intent(instrument),
        )


def test_provider_order_fill_aggregate_rejects_tampering_and_public_construction(
    adapter, instrument
):
    aggregate = adapter.aggregate_historical_order_fills(
        [_history_row()],
        expected_broker_order_id="1234",
        expected_local_order=_intent(instrument),
    )
    with pytest.raises(TypeError):
        ProviderOrderFillAggregate()
    object.__setattr__(aggregate, "filled_quantity", Decimal("0.4"))
    assert not aggregate.verify_hash()


def test_provider_order_fill_hash_commits_exact_fill_price_and_timestamp(adapter, instrument):
    expected = _intent(instrument)
    base = adapter.aggregate_historical_order_fills(
        [_history_row()],
        expected_broker_order_id="1234",
        expected_local_order=expected,
    )
    changed_price = adapter.aggregate_historical_order_fills(
        [_history_row(fill_price=501.26)],
        expected_broker_order_id="1234",
        expected_local_order=expected,
    )
    changed_time = adapter.aggregate_historical_order_fills(
        [_history_row(filled_at="2026-10-01T12:02:00Z")],
        expected_broker_order_id="1234",
        expected_local_order=expected,
    )
    assert base.aggregate_id != changed_price.aggregate_id
    assert base.aggregate_id != changed_time.aggregate_id


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "FUTURE_PROVIDER_STATE"},
        {"ticker": "UNKNOWN_EQ"},
        {"currency": "EUR"},
        {"filledQuantity": 3},
        {"status": "FILLED", "filledQuantity": 2},
        {"status": "PARTIALLY_FILLED", "filledQuantity": 0},
        {"createdAt": "2026-10-01T12:00:00"},
        {"side": "SHORT"},
    ],
)
def test_unrecognized_or_inconsistent_provider_order_fails_closed(adapter, changes) -> None:
    if changes == {"status": "FUTURE_PROVIDER_STATE"}:
        mapped = adapter.parse_order(_provider_order(**changes))
        assert mapped.status.provider.value == "UNKNOWN"
        assert mapped.status.internal is None and mapped.status.blocked
        return
    with pytest.raises(ProviderSchemaError):
        adapter.parse_order(_provider_order(**changes))


def test_unknown_response_fields_and_unsupported_provider_order_type_block(adapter) -> None:
    with pytest.raises(ProviderSchemaError, match="outside the reviewed schema"):
        adapter.parse_order(_provider_order(unreviewedField="x"))
    with pytest.raises(ProviderSchemaError, match="Only provider LIMIT"):
        adapter.parse_order(_provider_order(type="MARKET"))


def test_rate_limit_header_parser_checks_consistency() -> None:
    headers = {
        "x-ratelimit-limit": "20",
        "x-ratelimit-period": "60",
        "x-ratelimit-remaining": "17",
        "x-ratelimit-reset": "1790870000",
        "x-ratelimit-used": "3",
    }
    assert parse_rate_limit_headers(headers) == {
        "limit": 20,
        "period_seconds": 60.0,
        "remaining": 17,
        "reset_epoch": 1790870000.0,
        "used": 3,
    }
    with pytest.raises(ProviderSchemaError):
        parse_rate_limit_headers({**headers, "x-ratelimit-remaining": "18"})
    with pytest.raises(ProviderSchemaError):
        parse_rate_limit_headers(
            {key: value for key, value in headers.items() if key != "x-ratelimit-reset"}
        )


def test_retry_after_seconds_and_http_date_are_parsed_but_malformed_blocks() -> None:
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert parse_retry_after("5", now=now) == datetime(2026, 10, 1, 12, 0, 5, tzinfo=UTC)
    assert parse_retry_after(None, now=now) is None
    with pytest.raises(ProviderSchemaError):
        parse_retry_after("later", now=now)
    with pytest.raises(ProviderSchemaError):
        parse_retry_after("86401", now=now)
