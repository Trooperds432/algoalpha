"""Canonical event serialization; no executable deserialization."""

import json
from datetime import datetime
from decimal import Decimal

from .clock import utc
from .events import Event
from .exceptions import DomainError
from .identifiers import RunId


def decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError("A finite Decimal is required")
    return str(value)


def parse_decimal(value: str) -> Decimal:
    if not isinstance(value, str):
        raise DomainError("Decimal input must be a string, never a float")
    try:
        parsed = Decimal(value)
    except ArithmeticError as exc:
        raise DomainError("Invalid decimal") from exc
    if not parsed.is_finite():
        raise DomainError("Decimal must be finite")
    return parsed


def event_json(event: Event) -> str:
    return json.dumps(
        {
            "schema_version": event.schema_version,
            "event_id": event.event_id,
            "run_id": event.run_id.value,
            "sequence": event.sequence,
            "event_time": utc(event.event_time).isoformat(),
            "observed_at": utc(event.observed_at).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def parse_event(text: str) -> Event:
    fields = {"schema_version", "event_id", "run_id", "sequence", "event_time", "observed_at"}
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != fields:
            raise DomainError("Unexpected event fields")
        return Event(
            event_id=data["event_id"],
            run_id=RunId(data["run_id"]),
            sequence=data["sequence"],
            event_time=datetime.fromisoformat(data["event_time"]),
            observed_at=datetime.fromisoformat(data["observed_at"]),
            schema_version=data["schema_version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError("Invalid event serialization") from exc
