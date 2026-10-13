"""Validated, immutable source-data records. Prices remain exact Decimal values."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{field} must be a nonempty string")
    return value


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal):
        raise DomainError(f"{field} must be Decimal, not a binary float")
    if not value.is_finite():
        raise DomainError(f"{field} must be finite")
    if (positive and value <= 0) or (not positive and value < 0):
        raise DomainError(f"{field} must be {'positive' if positive else 'nonnegative'}")
    return value


@dataclass(frozen=True, slots=True)
class InstrumentMeta:
    instrument_id: InstrumentId
    symbol: str
    currency: str
    asset_class: str
    calendar: str
    timezone: str
    tick_size: Decimal
    quantity_step: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, InstrumentId):
            raise DomainError("instrument_id must be an InstrumentId")
        _text(self.symbol, "symbol")
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isalpha()
            or not self.currency.isupper()
        ):
            raise DomainError("currency must be a three-letter uppercase code")
        if self.asset_class not in ("equity", "etf"):
            raise DomainError("asset_class must be equity or etf")
        if self.calendar != "XNYS":
            raise DomainError("Only the XNYS calendar is supported")
        try:
            ZoneInfo(_text(self.timezone, "timezone"))
        except ZoneInfoNotFoundError as exc:
            raise DomainError("timezone is not a known IANA timezone") from exc
        if self.timezone != "America/New_York":
            raise DomainError("XNYS instruments require America/New_York timezone")
        _decimal(self.tick_size, "tick_size", positive=True)
        _decimal(self.quantity_step, "quantity_step", positive=True)


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: InstrumentId
    frequency: str
    start: datetime
    end: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, InstrumentId):
            raise DomainError("instrument_id must be an InstrumentId")
        if self.frequency not in ("1d", "1m"):
            raise DomainError("frequency must be 1d or 1m")
        for field in ("start", "end", "available_at"):
            object.__setattr__(self, field, utc(getattr(self, field)))
        if self.start >= self.end:
            raise DomainError("Bar interval requires start < end")
        if self.available_at < self.end:
            raise DomainError("Bar available_at cannot precede end")
        for field in ("open", "high", "low", "close"):
            _decimal(getattr(self, field), f"{field} price", positive=True)
        _decimal(self.volume, "volume")
        if self.high < max(self.open, self.close, self.low):
            raise DomainError("high must bound open, close and low")
        if self.low > min(self.open, self.close, self.high):
            raise DomainError("low must bound open, close and high")
        _text(self.source, "source")


@dataclass(frozen=True, slots=True)
class CorporateAction:
    action_id: str
    instrument_id: InstrumentId
    action_type: str
    known_at: datetime
    effective_at: datetime
    ratio: Decimal | None = None
    amount: Decimal | None = None
    ex_date: date | None = None
    pay_date: date | None = None

    def __post_init__(self) -> None:
        _text(self.action_id, "action_id")
        if not isinstance(self.instrument_id, InstrumentId):
            raise DomainError("instrument_id must be an InstrumentId")
        for field in ("known_at", "effective_at"):
            object.__setattr__(self, field, utc(getattr(self, field)))
        if self.action_type == "split":
            _decimal(self.ratio, "split ratio", positive=True)
            if self.amount is not None or self.pay_date is not None:
                raise DomainError("split cannot include cash amount or pay_date")
        elif self.action_type == "dividend":
            _decimal(self.amount, "dividend amount", positive=True)
            if self.ratio is not None or self.ex_date is None or self.pay_date is None:
                raise DomainError("dividend requires ex_date and pay_date, without ratio")
        else:
            raise DomainError("action_type must be split or dividend")
        if self.ex_date is not None and not isinstance(self.ex_date, date):
            raise DomainError("ex_date must be a date")
        if self.pay_date is not None and not isinstance(self.pay_date, date):
            raise DomainError("pay_date must be a date")
        if self.ex_date is not None and self.pay_date is not None and self.pay_date < self.ex_date:
            raise DomainError("pay_date cannot precede ex_date")
