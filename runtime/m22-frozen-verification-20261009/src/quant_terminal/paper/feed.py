"""Bounded accepted-bar history with the historical strategy data-access contract."""

from collections import deque
from datetime import datetime
from threading import RLock

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError, FutureDataError, TimeOrderError
from quant_terminal.data.model import Bar, InstrumentMeta

from .clock import LiveClock


class RealtimeDataFeed:
    """Hold trusted accepted bars, without network or broker connectivity.

    Event identity, sequence gaps, duplicates and staleness are session concerns.
    This feed admits only completed, available bars for one instrument and frequency.
    The retention limit applies equally to restored and newly received history.
    """

    def __init__(
        self, instrument: InstrumentMeta, clock: LiveClock, history_limit: int = 10000
    ) -> None:
        if not isinstance(instrument, InstrumentMeta) or not isinstance(clock, LiveClock):
            raise DomainError("Realtime feed requires instrument metadata and a live clock")
        if type(history_limit) is not int or not 1 <= history_limit <= 10000:
            raise DomainError("History limit must be an integer between 1 and 10000")
        self._instrument = instrument
        self._clock = clock
        self._bars: deque[Bar] = deque(maxlen=history_limit)
        self._lock = RLock()

    @property
    def bars(self) -> tuple[Bar, ...]:
        with self._lock:
            return tuple(self._bars)

    def append(self, bar: Bar) -> None:
        with self._lock:
            if not isinstance(bar, Bar) or bar.instrument_id != self._instrument.instrument_id:
                raise DomainError("Realtime bar must match the configured instrument")
            if bar.available_at > self._clock.now:
                raise FutureDataError("Realtime bar is not yet completed and available")
            if self._bars:
                previous = self._bars[-1]
                if bar.frequency != previous.frequency:
                    raise DomainError("Realtime bar frequency cannot change within a feed")
                if bar.start <= previous.start or bar.start < previous.end:
                    raise TimeOrderError("Realtime bars must be chronological and nonoverlapping")
            self._bars.append(bar)

    def history(self, *, end: datetime, count: int) -> tuple[Bar, ...]:
        end_utc = utc(end)
        if type(count) is not int or count <= 0:
            raise DomainError("History count must be a positive integer")
        with self._lock:
            if end_utc > self._clock.now:
                raise FutureDataError("Requested history end extends beyond live time")
            visible = tuple(
                bar
                for bar in self._bars
                if bar.start < end_utc and bar.end <= end_utc and bar.available_at <= end_utc
            )
            return visible[-count:]

    def between(self, *, start: datetime, end: datetime) -> tuple[Bar, ...]:
        start_at, end_at = utc(start), utc(end)
        if start_at >= end_at:
            raise DomainError("Data interval must have positive duration")
        with self._lock:
            if end_at > self._clock.now:
                raise FutureDataError("Requested interval extends beyond live time")
            return tuple(
                bar
                for bar in self._bars
                if start_at <= bar.start < end_at
                and bar.end <= end_at
                and bar.available_at <= end_at
            )
