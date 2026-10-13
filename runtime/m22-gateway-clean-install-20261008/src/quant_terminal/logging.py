"""Structured logs redact named sensitive fields, not arbitrary secret strings."""

import json
from datetime import UTC, datetime
from typing import TextIO

SENSITIVE = {"authorization", "cookie", "password", "secret", "token", "apikey", "credential"}


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(
                field in "".join(c for c in str(key).lower() if c.isalnum()) for field in SENSITIVE
            )
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return "[UNSUPPORTED_VALUE]"


def write_event(stream: TextIO, event_name: str, fields: dict[str, object]) -> None:
    if not event_name or not all(c.isalnum() or c in "_.-" for c in event_name):
        raise ValueError("Event name must be an identifier, not free text")
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event_name,
        "fields": redact(fields),
    }
    stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    stream.flush()
