"""Immutable event identity and causal observation timestamps."""

from dataclasses import dataclass
from datetime import datetime

from .clock import utc
from .exceptions import DomainError
from .identifiers import RunId


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    run_id: RunId
    sequence: int
    event_time: datetime
    observed_at: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise DomainError("run_id must be a RunId")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise DomainError("event_id is required")
        if type(self.sequence) is not int or self.sequence < 0:
            raise DomainError("sequence must be a nonnegative integer")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise DomainError("Unsupported event schema")
        event_time, observed_at = utc(self.event_time), utc(self.observed_at)
        if observed_at < event_time:
            raise DomainError("Observation cannot precede the economic event")
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "observed_at", observed_at)
