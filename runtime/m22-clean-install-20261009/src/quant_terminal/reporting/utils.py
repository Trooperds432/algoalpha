"""Shared locale-independent formatting and Decimal policies for reports."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context


def decimal_context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


def iso_utc(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None
