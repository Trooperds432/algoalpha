from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quant_terminal.core.exceptions import DomainError, FutureDataError, TimeOrderError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import Bar, InstrumentMeta
from quant_terminal.paper.clock import LiveClock
from quant_terminal.paper.feed import RealtimeDataFeed

START = datetime(2026, 10, 1, 14, tzinfo=UTC)
INSTRUMENT = InstrumentMeta(
    InstrumentId("SPY"),
    "SPY",
    "USD",
    "etf",
    "XNYS",
    "America/New_York",
    Decimal("0.01"),
    Decimal("1"),
)


def bar(index: int, *, delay: int = 0) -> Bar:
    start = START + timedelta(minutes=index)
    end = start + timedelta(minutes=1)
    return Bar(
        INSTRUMENT.instrument_id,
        "1m",
        start,
        end,
        end + timedelta(minutes=delay),
        Decimal("100"),
        Decimal("101"),
        Decimal("99"),
        Decimal("100"),
        Decimal("1000"),
        "synthetic-paper-fixture",
    )


def test_live_clock_normalizes_utc_and_rejects_backward_time() -> None:
    observations = iter(
        [
            START.astimezone(timezone(timedelta(hours=1))),
            START,
            START - timedelta(microseconds=1),
            START + timedelta(seconds=1),
        ]
    )
    clock = LiveClock(lambda: next(observations))
    assert clock.now == START
    assert clock.now.tzinfo is UTC
    with pytest.raises(TimeOrderError):
        _ = clock.now
    assert clock.now == START + timedelta(seconds=1)


def test_clock_rejects_naive_time_and_invalid_reader() -> None:
    with pytest.raises(DomainError):
        _ = LiveClock(lambda: START.replace(tzinfo=None)).now
    with pytest.raises(DomainError):
        LiveClock(3)  # type: ignore[arg-type]
    assert LiveClock().now.tzinfo is UTC


def test_history_has_no_future_or_unavailable_bars() -> None:
    clock = LiveClock(lambda: START + timedelta(minutes=10))
    feed = RealtimeDataFeed(INSTRUMENT, clock)
    first, delayed, third = bar(0), bar(1, delay=3), bar(2)
    for item in (first, delayed, third):
        feed.append(item)
    assert feed.history(end=START + timedelta(minutes=3), count=10) == (first, third)
    assert feed.history(end=START + timedelta(minutes=5), count=2) == (delayed, third)
    assert feed.history(end=START, count=2) == ()
    with pytest.raises(FutureDataError):
        feed.history(end=START + timedelta(minutes=11), count=1)
    with pytest.raises(DomainError):
        feed.history(end=START.replace(tzinfo=None), count=1)


@pytest.mark.parametrize("count", [0, -1, True, 1.5, "1"])
def test_history_rejects_invalid_count(count: object) -> None:
    feed = RealtimeDataFeed(INSTRUMENT, LiveClock(lambda: START))
    with pytest.raises(DomainError):
        feed.history(end=START, count=count)  # type: ignore[arg-type]


def test_bounded_retention_and_immutable_snapshot() -> None:
    feed = RealtimeDataFeed(
        INSTRUMENT, LiveClock(lambda: START + timedelta(minutes=10)), history_limit=2
    )
    feed.append(bar(0))
    snapshot = feed.bars
    feed.append(bar(1))
    feed.append(bar(2))
    assert snapshot == (bar(0),)
    assert feed.bars == (bar(1), bar(2))
    assert feed.history(end=START + timedelta(minutes=10), count=100) == feed.bars


@pytest.mark.parametrize("limit", [0, -1, True, 10001, 1.5])
def test_retention_limit_is_bounded(limit: object) -> None:
    with pytest.raises(DomainError):
        RealtimeDataFeed(INSTRUMENT, LiveClock(), history_limit=limit)  # type: ignore[arg-type]


def test_invalid_append_does_not_mutate_feed() -> None:
    feed = RealtimeDataFeed(INSTRUMENT, LiveClock(lambda: START + timedelta(minutes=2)))
    first = bar(0)
    feed.append(first)
    for invalid in (
        first,
        bar(-1),
        bar(2),
        bar(1, delay=2),
        replace(bar(1), instrument_id=InstrumentId("QQQ")),
        replace(bar(1), frequency="1d"),
        replace(bar(1), start=START + timedelta(seconds=30)),
    ):
        with pytest.raises(DomainError):
            feed.append(invalid)
        assert feed.bars == (first,)
    with pytest.raises(DomainError):
        feed.append("bar")  # type: ignore[arg-type]
    feed.append(bar(1))
    assert feed.bars == (first, bar(1))


def test_restart_can_restore_accepted_historical_bars() -> None:
    clock = LiveClock(lambda: START + timedelta(days=1))
    original = RealtimeDataFeed(INSTRUMENT, clock)
    original.append(bar(0))
    original.append(bar(1))
    restored = RealtimeDataFeed(INSTRUMENT, clock)
    for item in original.bars:
        restored.append(item)
    assert restored.history(end=bar(1).available_at, count=10) == original.bars


def test_feed_detects_clock_rollback_before_mutating() -> None:
    instant = START + timedelta(minutes=2)
    clock = LiveClock(lambda: instant)
    feed = RealtimeDataFeed(INSTRUMENT, clock)
    feed.append(bar(0))
    instant = START
    with pytest.raises(TimeOrderError):
        feed.append(bar(1))
    assert feed.bars == (bar(0),)
