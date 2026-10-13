import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quant_terminal.core.clock import SimulationClock, utc
from quant_terminal.core.events import Event
from quant_terminal.core.exceptions import DomainError, TimeOrderError
from quant_terminal.core.identifiers import FillId, InstrumentId, OrderId, RunId, StrategyId
from quant_terminal.core.serialization import decimal_text, event_json, parse_decimal, parse_event

T = datetime(2026, 1, 1, tzinfo=UTC)


def test_identifiers_are_distinct_and_immutable():
    assert RunId("same") != OrderId("same")
    for cls in (RunId, StrategyId, OrderId, FillId, InstrumentId):
        assert len({cls.new() for _ in range(100)}) == 100
        with pytest.raises(FrozenInstanceError):
            cls("a").value = "b"
        with pytest.raises(DomainError):
            cls("../secret")


def test_time_normalization_and_rewind_rejection():
    local = datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=1)))
    assert utc(local) == T
    clock = SimulationClock(local)
    assert clock.advance(T) == T
    clock.advance(T + timedelta(minutes=1))
    with pytest.raises(TimeOrderError):
        clock.advance(T)
    assert SimulationClock.restore(clock.snapshot()).now == clock.now
    with pytest.raises(DomainError):
        SimulationClock(datetime(2026, 1, 1))


@pytest.mark.parametrize("snapshot", ["malformed", "2026-01-01T00:00:00", None, 42])
def test_invalid_clock_snapshot_raises_domain_error(snapshot):
    with pytest.raises(DomainError):
        SimulationClock.restore(snapshot)


def test_event_causal_roundtrip():
    event = Event("fill-1", RunId("run-1"), 4, T, T + timedelta(minutes=1))
    assert "observed_at" in json.loads(event_json(event))
    assert "available_at" not in json.loads(event_json(event))
    assert parse_event(event_json(event)) == event
    assert event_json(parse_event(event_json(event))) == event_json(event)
    with pytest.raises(DomainError):
        Event("x", RunId("r"), 0, T, T - timedelta(seconds=1))
    with pytest.raises(DomainError):
        Event("x", RunId("r"), True, T, T)
    with pytest.raises(DomainError):
        parse_event('{"unexpected":true}')


@given(st.integers(min_value=0, max_value=1000000))
def test_clock_replay_is_deterministic(seconds):
    expected = T + timedelta(seconds=seconds)
    clock = SimulationClock(T)
    assert clock.advance(expected) == expected
    assert SimulationClock.restore(clock.snapshot()).now == expected


@given(st.decimals(allow_nan=False, allow_infinity=False, places=8))
def test_exact_decimal_roundtrip(value):
    assert parse_decimal(decimal_text(value)) == value


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "bad"])
def test_invalid_decimal_rejected(value):
    with pytest.raises(DomainError):
        parse_decimal(value)


def test_float_decimal_input_rejected():
    with pytest.raises(DomainError):
        parse_decimal(0.1)
    assert parse_decimal("0.1") == Decimal("0.1")
