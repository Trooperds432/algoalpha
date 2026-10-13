"""Clock-scoped strategy data access; internal snapshot queries stay behind this boundary."""

from __future__ import annotations

from datetime import datetime

from quant_terminal.core.clock import SimulationClock, utc
from quant_terminal.core.exceptions import DomainError, FutureDataError

from .model import Bar
from .storage import DatasetSnapshot, DatasetStore


class DataFeed:
    """Expose only bars known to the strategy at the simulation clock time.

    This is an application API for trusted strategies, not an adversarial Python
    sandbox. The caller must not also receive the underlying DatasetStore.
    """

    def __init__(
        self, store: DatasetStore, snapshot: DatasetSnapshot, clock: SimulationClock
    ) -> None:
        if not isinstance(store, DatasetStore) or not isinstance(snapshot, DatasetSnapshot):
            raise DomainError("A dataset store and immutable snapshot are required")
        if not isinstance(clock, SimulationClock):
            raise DomainError("A simulation clock is required")
        self._store = store
        self._snapshot = snapshot
        self._clock = clock

    def between(self, *, start: datetime, end: datetime) -> tuple[Bar, ...]:
        """Return visible bars with start in [start, end); reject future requests."""
        start_utc, end_utc = utc(start), utc(end)
        if start_utc >= end_utc:
            raise DomainError("Data interval must have positive duration")
        if end_utc > self._clock.now:
            raise FutureDataError("Requested interval extends beyond simulation time")
        return tuple(
            bar
            for bar in self._store.query_bars(self._snapshot, start=start_utc, end=end_utc)
            if bar.end <= end_utc and bar.available_at <= end_utc
        )

    def history(self, *, end: datetime, count: int) -> tuple[Bar, ...]:
        """Return up to count most recent visible bars strictly before end by start."""
        end_utc = utc(end)
        if end_utc > self._clock.now:
            raise FutureDataError("Requested history end extends beyond simulation time")
        if type(count) is not int or count <= 0:
            raise DomainError("History count must be a positive integer")
        visible = (
            bar
            for bar in self._store.query_bars(self._snapshot, end=end_utc)
            if bar.end <= end_utc and bar.available_at <= end_utc
        )
        return tuple(list(visible)[-count:])
