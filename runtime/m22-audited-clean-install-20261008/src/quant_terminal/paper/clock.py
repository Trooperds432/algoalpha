"""Injected UTC wall clock; fail closed rather than conceal clock rollback."""

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError, TimeOrderError


def _system_utc() -> datetime:
    return datetime.now(UTC)


class LiveClock:
    def __init__(self, read: Callable[[], datetime] = _system_utc) -> None:
        if not callable(read):
            raise DomainError("Live clock requires a timestamp reader")
        self._read = read
        self._last: datetime | None = None
        self._lock = Lock()

    @property
    def now(self) -> datetime:
        with self._lock:
            observed = utc(self._read())
            if self._last is not None and observed < self._last:
                raise TimeOrderError("Live clock cannot move backwards")
            self._last = observed
            return observed
