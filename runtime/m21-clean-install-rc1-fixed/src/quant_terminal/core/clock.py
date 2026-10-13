"""Observable simulation time must be UTC-aware and monotonic."""

from datetime import UTC, datetime

from .exceptions import DomainError, TimeOrderError


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainError("Timestamp must be timezone-aware")
    return value.astimezone(UTC)


class SimulationClock:
    def __init__(self, initial: datetime) -> None:
        self._now = utc(initial)

    @property
    def now(self) -> datetime:
        return self._now

    def advance(self, observed_at: datetime) -> datetime:
        value = utc(observed_at)
        if value < self._now:
            raise TimeOrderError("Observable time cannot move backwards")
        self._now = value
        return value

    def snapshot(self) -> str:
        return self._now.isoformat()

    @classmethod
    def restore(cls, timestamp: str) -> "SimulationClock":
        if not isinstance(timestamp, str):
            raise DomainError("Clock snapshot must be an ISO timestamp string")
        try:
            return cls(datetime.fromisoformat(timestamp))
        except ValueError as exc:
            raise DomainError("Clock snapshot must be a valid aware ISO timestamp") from exc
