"""Immutable backtest configuration and result snapshots."""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from quant_terminal.accounting.ledger import PositionSnapshot
from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.orders.models import Fill, OrderSnapshot, Trade


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: Decimal
    currency: str
    max_order_notional: Decimal = Decimal("1000000000")
    allow_short: bool = False
    short_assumption: str | None = None
    allow_zero_costs: bool = False

    def __post_init__(self) -> None:
        for field, value in (
            ("initial_cash", self.initial_cash),
            ("max_order_notional", self.max_order_notional),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise DomainError(f"{field} must be a positive finite Decimal")
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isalpha()
            or not self.currency.isupper()
        ):
            raise DomainError("currency must be a three-letter uppercase code")
        if type(self.allow_short) is not bool:
            raise DomainError("allow_short must be boolean")
        if self.allow_short and (
            not isinstance(self.short_assumption, str) or not self.short_assumption.strip()
        ):
            raise DomainError("Shorting requires an explicit research assumption")
        if not self.allow_short and self.short_assumption is not None:
            raise DomainError("short_assumption requires allow_short=True")
        if type(self.allow_zero_costs) is not bool:
            raise DomainError("allow_zero_costs must be boolean")


@dataclass(frozen=True, slots=True)
class NAVPoint:
    timestamp: datetime
    nav: Decimal
    period_return: Decimal | None
    cash: Decimal
    positions: tuple[PositionSnapshot, ...]
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    long_exposure: Decimal
    short_exposure: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    leverage: Decimal | None
    reconciliation_error: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", utc(self.timestamp))


@dataclass(frozen=True, slots=True)
class BenchmarkPoint:
    timestamp: datetime
    instrument_id: InstrumentId
    price: Decimal
    value: Decimal
    price_return_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", utc(self.timestamp))


@dataclass(frozen=True, slots=True)
class BacktestResult:
    strategy_name: str
    start: datetime
    end: datetime
    nav: tuple[NAVPoint, ...]
    orders: tuple[OrderSnapshot, ...]
    fills: tuple[Fill, ...]
    trades: tuple[Trade, ...]
    commission_cost: Decimal
    slippage_cost: Decimal
    benchmark: tuple[BenchmarkPoint, ...]
    metadata: tuple[tuple[str, str], ...]

    @property
    def cash(self) -> tuple[tuple[datetime, Decimal], ...]:
        return tuple((point.timestamp, point.cash) for point in self.nav)

    @property
    def positions(self) -> tuple[tuple[datetime, tuple[PositionSnapshot, ...]], ...]:
        return tuple((point.timestamp, point.positions) for point in self.nav)

    @property
    def returns(self) -> tuple[tuple[datetime, Decimal | None], ...]:
        return tuple((point.timestamp, point.period_return) for point in self.nav)

    @property
    def exposures(self) -> tuple[tuple[datetime, Decimal, Decimal, Decimal | None], ...]:
        return tuple(
            (point.timestamp, point.gross_exposure, point.net_exposure, point.leverage)
            for point in self.nav
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        if self.end <= self.start:
            raise DomainError("Backtest end must follow start")

    @property
    def total_cost(self) -> Decimal:
        with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
            return self.commission_cost + self.slippage_cost
