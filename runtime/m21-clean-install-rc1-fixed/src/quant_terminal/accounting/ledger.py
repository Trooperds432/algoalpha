"""Decimal FIFO cash and position accounting for equities and ETFs."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import FillId, InstrumentId
from quant_terminal.data.model import InstrumentMeta

PRECISION = 34


def _decimal_context() -> Context:
    """Create a context isolated from caller precision, rounding and traps."""
    return Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)


class FillSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


def _amount(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError(f"{name} must be a finite Decimal")
    if (positive and value <= 0) or (not positive and value < 0):
        raise DomainError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return value


def _multiple(value: Decimal, step: Decimal) -> bool:
    """Test a Decimal multiple with integer coefficients, independent of context."""
    value_tuple, step_tuple = value.as_tuple(), step.as_tuple()
    if not isinstance(value_tuple.exponent, int) or not isinstance(step_tuple.exponent, int):
        return False
    exponent = min(value_tuple.exponent, step_tuple.exponent)
    value_units: int = int("".join(map(str, value_tuple.digits)) or "0") * 10 ** (
        value_tuple.exponent - exponent
    )
    step_units: int = int("".join(map(str, step_tuple.digits)) or "0") * 10 ** (
        step_tuple.exponent - exponent
    )
    if step_units == 0:
        return False
    return bool(value_units % step_units == 0)


def _mul(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_decimal_context()):
        return left * right


def _add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_decimal_context()):
        return left + right


def _sub(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_decimal_context()):
        return left - right


def _sum(values: Iterable[Decimal]) -> Decimal:
    with localcontext(_decimal_context()):
        return sum(values, Decimal(0))


def _ratio(total: Decimal, numerator: Decimal, denominator: Decimal) -> Decimal:
    if numerator == denominator:
        return total
    with localcontext(_decimal_context()):
        return total * numerator / denominator


@dataclass(frozen=True, slots=True)
class LedgerFill:
    fill_id: FillId
    instrument: InstrumentMeta
    side: FillSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    timestamp: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.fill_id, FillId):
            raise DomainError("fill_id must be a FillId")
        if not isinstance(self.instrument, InstrumentMeta):
            raise DomainError("instrument metadata is required")
        if not isinstance(self.side, FillSide):
            raise DomainError("side must be a FillSide")
        _amount(self.quantity, "quantity", positive=True)
        _amount(self.price, "price", positive=True)
        _amount(self.fee, "fee")
        object.__setattr__(self, "timestamp", utc(self.timestamp))


@dataclass(slots=True)
class _Lot:
    quantity: Decimal
    entry_price: Decimal
    fee_remaining: Decimal


@dataclass(slots=True)
class _Position:
    instrument: InstrumentMeta
    lots: list[_Lot]
    mark: Decimal
    realized_pnl: Decimal = Decimal(0)

    @property
    def quantity(self) -> Decimal:
        return _sum(lot.quantity for lot in self.lots)


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    instrument_id: InstrumentId
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    mark_price: Decimal
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    currency: str
    initial_cash: Decimal
    cash: Decimal
    positions: tuple[PositionSnapshot, ...]
    nav: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    long_exposure: Decimal
    short_exposure: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    leverage: Decimal | None
    leverage_reason: str | None
    reconciliation_error: Decimal


class PortfolioLedger:
    """Single-currency FIFO ledger; M3 records fills and marks, not orders."""

    def __init__(
        self,
        *,
        initial_cash: Decimal,
        currency: str,
        allow_short: bool = False,
        short_assumption: str | None = None,
    ) -> None:
        _amount(initial_cash, "initial_cash", positive=True)
        if not isinstance(currency, str) or len(currency) != 3 or currency != currency.upper():
            raise DomainError("currency must be a three-letter uppercase code")
        if type(allow_short) is not bool:
            raise DomainError("allow_short must be boolean")
        if allow_short and (not isinstance(short_assumption, str) or not short_assumption.strip()):
            raise DomainError("Short positions require an explicit research-only assumption")
        if not allow_short and short_assumption is not None:
            raise DomainError("short_assumption requires allow_short=True")
        self.initial_cash = initial_cash
        self.currency = currency
        self.allow_short = allow_short
        self.short_assumption = short_assumption
        self._cash = initial_cash
        self._positions: dict[InstrumentId, _Position] = {}
        self._processed: set[FillId] = set()
        self._fills: list[LedgerFill] = []

    @property
    def fills(self) -> tuple[LedgerFill, ...]:
        return tuple(self._fills)

    def apply_fill(self, fill: LedgerFill) -> PortfolioSnapshot:
        if not isinstance(fill, LedgerFill):
            raise DomainError("LedgerFill is required")
        if fill.fill_id in self._processed:
            raise DomainError("Duplicate fill_id")
        if fill.instrument.currency != self.currency:
            raise DomainError("Multi-currency accounting is unsupported")
        if not _multiple(fill.quantity, fill.instrument.quantity_step):
            raise DomainError("Fill quantity violates instrument quantity step")
        if not _multiple(fill.price, fill.instrument.tick_size):
            raise DomainError("Fill price violates instrument tick size")

        instrument_id = fill.instrument.instrument_id
        original = self._positions.get(instrument_id)
        if original is not None and original.instrument != fill.instrument:
            raise DomainError("Instrument metadata changed within a ledger")
        position = (
            deepcopy(original)
            if original is not None
            else _Position(instrument=fill.instrument, lots=[], mark=fill.price)
        )
        direction = Decimal(1) if fill.side is FillSide.BUY else Decimal(-1)
        signed_quantity = (
            fill.quantity if fill.side is FillSide.BUY else fill.quantity.copy_negate()
        )
        current_quantity = position.quantity
        next_quantity = _add(current_quantity, signed_quantity)
        if not self.allow_short and next_quantity < 0:
            raise DomainError("Fill would create an unauthorized short position")

        remaining = fill.quantity
        matches: list[tuple[Decimal, Decimal, Decimal]] = []
        while (
            remaining > 0
            and position.lots
            and (
                (fill.side is FillSide.BUY and position.lots[0].quantity < 0)
                or (fill.side is FillSide.SELL and position.lots[0].quantity > 0)
            )
        ):
            lot = position.lots[0]
            lot_quantity = lot.quantity.copy_abs()
            matched = min(lot_quantity, remaining)
            entry_fee = (
                lot.fee_remaining
                if matched == lot_quantity
                else _ratio(lot.fee_remaining, matched, lot_quantity)
            )
            gross = _mul(_sub(fill.price, lot.entry_price), matched)
            if lot.quantity < 0:
                gross = gross.copy_negate()
            signed_match = matched if direction > 0 else matched.copy_negate()
            lot.quantity = _add(lot.quantity, signed_match)
            lot.fee_remaining = _sub(lot.fee_remaining, entry_fee)
            remaining = _sub(remaining, matched)
            matches.append((matched, gross, entry_fee))
            if lot.quantity == 0:
                position.lots.pop(0)

        closed_quantity = _sub(fill.quantity, remaining)
        closing_fee = (
            fill.fee if remaining == 0 else _ratio(fill.fee, closed_quantity, fill.quantity)
        )
        opening_fee = _sub(fill.fee, closing_fee)
        fee_left = closing_fee
        realized_delta = Decimal(0)
        for index, (matched, gross, entry_fee) in enumerate(matches):
            exit_fee = (
                fee_left
                if index == len(matches) - 1
                else _ratio(closing_fee, matched, closed_quantity)
            )
            fee_left = _sub(fee_left, exit_fee)
            realized_delta = _add(realized_delta, _sub(_sub(gross, entry_fee), exit_fee))
        position.realized_pnl = _add(position.realized_pnl, realized_delta)

        if remaining > 0:
            open_quantity = remaining if direction > 0 else remaining.copy_negate()
            position.lots.append(_Lot(open_quantity, fill.price, opening_fee))
        position.mark = fill.price
        cash_delta = _sub(_mul(signed_quantity.copy_negate(), fill.price), fill.fee)
        next_cash = _add(self._cash, cash_delta)

        previous_cash = self._cash
        self._cash = next_cash
        self._positions[instrument_id] = position
        self._processed.add(fill.fill_id)
        self._fills.append(fill)
        try:
            return self.snapshot()
        except Exception:
            self._cash = previous_cash
            if original is None:
                del self._positions[instrument_id]
            else:
                self._positions[instrument_id] = original
            self._processed.remove(fill.fill_id)
            self._fills.pop()
            raise

    def mark(self, instrument_id: InstrumentId, price: Decimal) -> PortfolioSnapshot:
        if not isinstance(instrument_id, InstrumentId):
            raise DomainError("instrument_id must be an InstrumentId")
        _amount(price, "mark price", positive=True)
        position = self._positions.get(instrument_id)
        if position is None or not position.lots:
            raise DomainError("Cannot mark an instrument without an open position")
        if not _multiple(price, position.instrument.tick_size):
            raise DomainError("Mark price violates instrument tick size")
        previous_mark = position.mark
        position.mark = price
        try:
            return self.snapshot()
        except Exception:
            position.mark = previous_mark
            raise

    def snapshot(self) -> PortfolioSnapshot:
        positions: list[PositionSnapshot] = []
        realized = Decimal(0)
        unrealized = Decimal(0)
        long_exposure = Decimal(0)
        short_exposure = Decimal(0)
        net_exposure = Decimal(0)
        for instrument_id, position in sorted(
            self._positions.items(), key=lambda item: item[0].value
        ):
            realized = _add(realized, position.realized_pnl)
            if not position.lots:
                continue
            quantity = position.quantity
            market_value = _mul(quantity, position.mark)
            absolute_quantity = quantity.copy_abs()
            entry_notional = _sum(
                _mul(lot.quantity.copy_abs(), lot.entry_price) for lot in position.lots
            )
            average_entry = _ratio(entry_notional, Decimal(1), absolute_quantity)
            position_unrealized = Decimal(0)
            for lot in position.lots:
                gross = _mul(_sub(position.mark, lot.entry_price), lot.quantity.copy_abs())
                if lot.quantity < 0:
                    gross = gross.copy_negate()
                position_unrealized = _add(position_unrealized, _sub(gross, lot.fee_remaining))
            unrealized = _add(unrealized, position_unrealized)
            if market_value >= 0:
                long_exposure = _add(long_exposure, market_value)
            else:
                short_exposure = _sub(short_exposure, market_value)
            net_exposure = _add(net_exposure, market_value)
            positions.append(
                PositionSnapshot(
                    instrument_id=instrument_id,
                    symbol=position.instrument.symbol,
                    quantity=quantity,
                    average_entry_price=average_entry,
                    mark_price=position.mark,
                    market_value=market_value,
                    realized_pnl=position.realized_pnl,
                    unrealized_pnl=position_unrealized,
                )
            )
        nav = _add(self._cash, net_exposure)
        gross_exposure = _add(long_exposure, short_exposure)
        leverage = None
        leverage_reason = "NAV must be positive to define leverage" if nav <= 0 else None
        if nav > 0:
            with localcontext(_decimal_context()):
                leverage = gross_exposure / nav
        reconciliation_error = _sub(_add(realized, unrealized), _sub(nav, self.initial_cash))
        return PortfolioSnapshot(
            currency=self.currency,
            initial_cash=self.initial_cash,
            cash=self._cash,
            positions=tuple(positions),
            nav=nav,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            long_exposure=long_exposure,
            short_exposure=short_exposure,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            leverage=leverage,
            leverage_reason=leverage_reason,
            reconciliation_error=reconciliation_error,
        )
