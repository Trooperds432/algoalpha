"""XNYS regular-session bounds and missing-bar diagnostics.

The NYSE holiday table supplies full closures and documented 13:00 local half
days. Session times use America/New_York so UTC offsets track daylight saving.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from holidays.constants import HALF_DAY, PUBLIC
from holidays.financial.ny_stock_exchange import NYSE

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError

from .model import Bar, InstrumentMeta

_NY = ZoneInfo("America/New_York")
_MINUTE = timedelta(minutes=1)


@lru_cache(maxsize=256)
def _closures(year: int, category: str) -> frozenset[date]:
    # python-holidays has no inline type annotations for its NYSE constructor.
    return frozenset(NYSE(years=year, categories=category))  # type: ignore[no-untyped-call]


def _session_bounds(value: datetime) -> tuple[datetime, datetime] | None:
    """Return aware UTC open/close, or None for a closed NYSE local date."""
    local_date = value.astimezone(_NY).date()
    if local_date.weekday() >= 5:
        return None
    if local_date in _closures(local_date.year, PUBLIC):
        return None
    half_day = local_date in _closures(local_date.year, HALF_DAY)
    opening = datetime.combine(local_date, time(9, 30), tzinfo=_NY).astimezone(UTC)
    closing = datetime.combine(local_date, time(13 if half_day else 16), tzinfo=_NY).astimezone(UTC)
    return opening, closing


def validate_bar(bar: Bar, instrument: InstrumentMeta) -> None:
    """Reject a bar that does not match the instrument's scheduled XNYS session."""
    if bar.instrument_id != instrument.instrument_id:
        raise DomainError("Bar instrument_id does not match instrument metadata")
    if instrument.calendar != "XNYS":
        raise DomainError("Only the XNYS calendar is supported")
    session = _session_bounds(bar.start)
    if session is None:
        raise DomainError("Bar falls on a closed XNYS session")
    opening, closing = session
    if bar.frequency == "1d":
        if bar.start != opening or bar.end != closing:
            raise DomainError("Daily bar must span the full XNYS session")
    elif bar.frequency == "1m":
        if not (opening <= bar.start < closing):
            raise DomainError("Minute bar starts outside the XNYS session")
        if bar.end != bar.start + _MINUTE or bar.end > closing:
            raise DomainError("Minute bar must span one scheduled session minute")
        if (bar.start - opening) % _MINUTE:
            raise DomainError("Minute bar start is not exchange-minute aligned")
    else:
        raise DomainError("Only 1d and 1m frequencies are supported")


def missing_bar_starts(
    bars: Sequence[Bar], instrument: InstrumentMeta, start: datetime, end: datetime
) -> tuple[datetime, ...]:
    """List scheduled bar starts absent from `[start, end)`; never fill them."""
    left, right = utc(start), utc(end)
    if left >= right:
        raise DomainError("Diagnostic interval requires start < end")
    if not bars:
        raise DomainError("At least one bar is needed to infer frequency")
    frequency = bars[0].frequency
    observed: set[datetime] = set()
    prior: datetime | None = None
    for bar in bars:
        validate_bar(bar, instrument)
        if bar.frequency != frequency:
            raise DomainError("Mixed frequencies are unsupported")
        if prior is not None and bar.start <= prior:
            raise DomainError("Bars must be ordered with no duplicate starts")
        if not (left <= bar.start < right):
            raise DomainError("Bar start is outside diagnostic interval")
        observed.add(bar.start)
        prior = bar.start

    missing: list[datetime] = []
    # Iterate the NY local dates intersecting the UTC interval.
    current = left.astimezone(_NY).date()
    last = right.astimezone(_NY).date()
    while current <= last:
        noon = datetime.combine(current, time(12), tzinfo=_NY)
        session = _session_bounds(noon)
        if session is not None:
            opening, closing = session
            if frequency == "1d":
                if left <= opening < right and opening not in observed:
                    missing.append(opening)
            else:
                minute = max(opening, left)
                if minute > opening:
                    elapsed = minute - opening
                    remainder = elapsed % _MINUTE
                    if remainder:
                        minute += _MINUTE - remainder
                while minute < min(closing, right):
                    if minute not in observed:
                        missing.append(minute)
                    minute += _MINUTE
        current += timedelta(days=1)
    return tuple(missing)
