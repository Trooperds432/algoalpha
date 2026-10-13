"""Exact, bounded wire contracts for persistent paper sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quant_terminal.application.runtime import _decimal, _integer, _object, _stamp, _text
from quant_terminal.backtesting.models import BacktestConfig
from quant_terminal.backtesting.strategies import (
    BuyAndHold,
    MovingAverageCross,
    SimpleMomentum,
    Strategy,
)
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import Bar, InstrumentMeta
from quant_terminal.execution.models import (
    ConfiguredSlippage,
    ExecutionConfig,
    PerUnitCommission,
    SlippageKind,
)


def instrument(value: object) -> InstrumentMeta:
    fields = {
        "instrument_id",
        "symbol",
        "currency",
        "asset_class",
        "calendar",
        "timezone",
        "tick_size",
        "quantity_step",
    }
    data = _object(value, fields, fields)
    return InstrumentMeta(
        InstrumentId(_text(data["instrument_id"])),
        _text(data["symbol"]),
        _text(data["currency"]),
        _text(data["asset_class"]),
        _text(data["calendar"]),
        _text(data["timezone"]),
        _decimal(data["tick_size"]),
        _decimal(data["quantity_step"]),
    )


def bar(value: object) -> Bar:
    fields = {
        "instrument_id",
        "frequency",
        "start",
        "end",
        "available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source",
    }
    data = _object(value, fields, fields)
    return Bar(
        InstrumentId(_text(data["instrument_id"])),
        _text(data["frequency"]),
        _stamp(data["start"]),
        _stamp(data["end"]),
        _stamp(data["available_at"]),
        _decimal(data["open"]),
        _decimal(data["high"]),
        _decimal(data["low"]),
        _decimal(data["close"]),
        _decimal(data["volume"]),
        _text(data["source"]),
    )


@dataclass(frozen=True)
class SessionConfig:
    instrument: InstrumentMeta
    strategy: Strategy
    accounting: BacktestConfig
    execution: ExecutionConfig
    stale_seconds: int


def config(value: object) -> SessionConfig:
    fields = {
        "instrument",
        "strategy",
        "parameters",
        "initial_cash",
        "max_order_notional",
        "execution",
        "stale_seconds",
    }
    data = _object(value, fields, fields)
    meta = instrument(data["instrument"])
    name = _text(data["strategy"])
    params_by_name = {
        "buy_and_hold": {"quantity"},
        "moving_average_cross": {"quantity", "fast_period", "slow_period"},
        "simple_momentum": {"quantity", "lookback"},
    }
    if name not in params_by_name:
        raise DomainError("Paper sessions support trusted built-in strategies only")
    params = _object(data["parameters"], params_by_name[name], params_by_name[name])
    quantity = _decimal(params["quantity"])
    from datetime import UTC

    from quant_terminal.core.identifiers import OrderId
    from quant_terminal.orders.models import OrderIntent, Side, TradeType

    # Validate quantity increments before durable session creation.
    OrderIntent(
        OrderId("paper-quantity-check"),
        meta,
        Side.BUY,
        TradeType.MARKET,
        quantity,
        datetime(2000, 1, 1, tzinfo=UTC),
    )
    strategy: Strategy
    if name == "buy_and_hold":
        strategy = BuyAndHold(meta.instrument_id, quantity)
    elif name == "moving_average_cross":
        strategy = MovingAverageCross(
            meta, quantity, _integer(params["fast_period"]), _integer(params["slow_period"])
        )
    else:
        strategy = SimpleMomentum(meta, quantity, _integer(params["lookback"]))
    ex_fields = {
        "commission_per_unit",
        "commission_minimum",
        "slippage_kind",
        "slippage_value",
        "maximum_participation",
        "zero_cost_assumption",
    }
    ex = _object(data["execution"], ex_fields, {"commission_per_unit"})
    try:
        slip = SlippageKind(_text(ex.get("slippage_kind", "none")))
    except ValueError as exc:
        raise DomainError("Unsupported paper slippage kind") from exc
    minimum = ex.get("commission_minimum")
    execution = ExecutionConfig(
        _decimal(ex.get("maximum_participation", "1")),
        PerUnitCommission(
            _decimal(ex["commission_per_unit"]), None if minimum is None else _decimal(minimum)
        ),
        ConfiguredSlippage(slip, _decimal(ex.get("slippage_value", "0"))),
        None if ex.get("zero_cost_assumption") is None else _text(ex["zero_cost_assumption"]),
    )
    zero_cost = (
        execution.commission.incremental_fee(Decimal(0), quantity, Decimal(0)) == 0
        and execution.slippage.amount(Decimal(1), Decimal(1)) == 0
    )
    if zero_cost and execution.zero_cost_assumption != "ZERO_COST_ASSUMPTION":
        raise DomainError("Zero-cost paper execution requires ZERO_COST_ASSUMPTION")
    stale = _integer(data["stale_seconds"])
    if not 1 <= stale <= 86400:
        raise DomainError("stale_seconds must be between 1 and 86400")
    accounting = BacktestConfig(
        _decimal(data["initial_cash"]),
        meta.currency,
        _decimal(data["max_order_notional"]),
        allow_zero_costs=zero_cost,
    )
    return SessionConfig(meta, strategy, accounting, execution, stale)


def observed(value: object) -> datetime:
    return _stamp(value)
