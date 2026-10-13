from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.calendar import missing_bar_starts, validate_bar
from quant_terminal.data.model import Bar, CorporateAction, InstrumentMeta

NY = ZoneInfo("America/New_York")
ID = InstrumentId("etf-1")


def instrument(**overrides: object) -> InstrumentMeta:
    fields = dict(
        instrument_id=ID,
        symbol="SPY",
        currency="USD",
        asset_class="etf",
        calendar="XNYS",
        timezone="America/New_York",
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("1"),
    )
    fields.update(overrides)
    return InstrumentMeta(**fields)


def bar(day: date, *, frequency: str = "1d", **overrides: object) -> Bar:
    opening = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    ending = datetime(day.year, day.month, day.day, 16, tzinfo=NY)
    if frequency == "1m":
        ending = opening + timedelta(minutes=1)
    fields = dict(
        instrument_id=ID,
        frequency=frequency,
        start=opening,
        end=ending,
        available_at=ending + timedelta(seconds=2),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.25"),
        volume=Decimal("1000"),
        source="synthetic",
    )
    fields.update(overrides)
    return Bar(**fields)


def test_bar_utc_normalization_interval_and_immutability() -> None:
    value = bar(date(2026, 7, 2))
    assert value.start == datetime(2026, 7, 2, 13, 30, tzinfo=UTC)
    assert value.end == datetime(2026, 7, 2, 20, tzinfo=UTC)
    assert value.available_at == value.end + timedelta(seconds=2)
    with pytest.raises(FrozenInstanceError):
        value.close = Decimal("200")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"start": datetime(2026, 7, 2, 9, 30)}, "aware"),
        ({"end": datetime(2026, 7, 2, 9, 30, tzinfo=NY)}, "interval"),
        ({"available_at": datetime(2026, 7, 2, 15, tzinfo=NY)}, "available"),
        ({"high": Decimal("99")}, "high"),
        ({"low": Decimal("100.5")}, "low"),
        ({"open": Decimal("0")}, "price"),
        ({"volume": Decimal("-1")}, "volume"),
        ({"close": Decimal("NaN")}, "finite"),
        ({"close": 100.25}, "Decimal"),
        ({"frequency": "1h"}, "frequency"),
        ({"source": ""}, "source"),
    ],
)
def test_bad_bars_are_rejected(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(DomainError, match=reason):
        bar(date(2026, 7, 2), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_id": "etf-1"},
        {"symbol": ""},
        {"calendar": "UNKNOWN"},
        {"timezone": "Bad/Zone"},
        {"timezone": "UTC"},
        {"asset_class": "crypto"},
        {"tick_size": Decimal("0")},
        {"quantity_step": Decimal("Infinity")},
    ],
)
def test_instrument_metadata_rejects_unsupported_and_invalid(changes: dict[str, object]) -> None:
    with pytest.raises(DomainError):
        instrument(**changes)


def test_corporate_action_timing_and_payload() -> None:
    known = datetime(2026, 6, 1, 12, tzinfo=UTC)
    effective = datetime(2026, 6, 5, 9, 30, tzinfo=NY)
    split = CorporateAction("split-1", ID, "split", known, effective, ratio=Decimal("2"))
    assert split.effective_at == datetime(2026, 6, 5, 13, 30, tzinfo=UTC)
    with pytest.raises(FrozenInstanceError):
        split.ratio = Decimal("3")
    dividend = CorporateAction(
        "div-1",
        ID,
        "dividend",
        known,
        effective,
        amount=Decimal("0.50"),
        ex_date=date(2026, 6, 5),
        pay_date=date(2026, 6, 15),
    )
    assert dividend.pay_date == date(2026, 6, 15)
    with pytest.raises(DomainError):
        CorporateAction("bad", ID, "split", known, effective, ratio=Decimal("0"))
    with pytest.raises(DomainError):
        CorporateAction("bad", ID, "dividend", known, effective, amount=Decimal("NaN"))
    with pytest.raises(DomainError):
        CorporateAction(
            "bad",
            ID,
            "dividend",
            known,
            effective,
            amount=Decimal("1"),
            ex_date=date(2026, 6, 5),
            pay_date=date(2026, 6, 4),
        )


def test_xnys_holiday_early_close_and_dst() -> None:
    inst = instrument()
    regular = bar(date(2026, 7, 2))
    validate_bar(regular, inst)
    early = bar(date(2026, 7, 3), end=datetime(2026, 7, 3, 13, tzinfo=NY))
    # Independence Day observed on July 3 in 2026, so no XNYS bar is valid.
    with pytest.raises(DomainError):
        validate_bar(early, inst)
    with pytest.raises(DomainError):
        validate_bar(bar(date(2026, 12, 25)), inst)
    validate_bar(bar(date(2026, 1, 5)), inst)
    assert bar(date(2026, 1, 5)).start.hour == 14
    assert regular.start.hour == 13
    half_day = bar(date(2026, 11, 27), end=datetime(2026, 11, 27, 13, tzinfo=NY))
    validate_bar(half_day, inst)
    with pytest.raises(DomainError):
        validate_bar(bar(date(2026, 11, 27)), inst)


def test_xnys_minute_boundary_and_missing_diagnostics() -> None:
    inst = instrument()
    day = date(2026, 7, 2)
    first = bar(day, frequency="1m")
    third = bar(
        day,
        frequency="1m",
        start=first.start + timedelta(minutes=2),
        end=first.end + timedelta(minutes=2),
        available_at=first.available_at + timedelta(minutes=2),
    )
    validate_bar(first, inst)
    assert missing_bar_starts([first, third], inst, first.start, third.end) == (
        first.start + timedelta(minutes=1),
    )
    before_open = bar(
        day,
        frequency="1m",
        start=first.start - timedelta(minutes=1),
        end=first.start,
        available_at=first.start,
    )
    with pytest.raises(DomainError):
        validate_bar(before_open, inst)


def test_calendar_instrument_mismatch_and_daily_missing_holiday_skip() -> None:
    inst = instrument()
    first = bar(date(2026, 7, 2))
    last = bar(date(2026, 7, 6))
    # July 3 is an observed holiday, weekend follows; no false missing bar.
    assert missing_bar_starts([first, last], inst, first.start, last.end) == ()
    with pytest.raises(DomainError):
        validate_bar(first, instrument(instrument_id=InstrumentId("other")))


def test_missing_regular_session_is_reported_without_fill() -> None:
    inst = instrument()
    first = bar(date(2026, 7, 6))
    last = bar(date(2026, 7, 8))
    missing = missing_bar_starts([first, last], inst, first.start, last.end)
    assert missing == (bar(date(2026, 7, 7)).start,)
    assert missing[0].tzinfo is UTC


def test_half_day_final_minute_and_close_exclusivity() -> None:
    inst = instrument()
    day = date(2026, 11, 27)
    final_start = datetime(2026, 11, 27, 12, 59, tzinfo=NY)
    final = bar(
        day,
        frequency="1m",
        start=final_start,
        end=final_start + timedelta(minutes=1),
        available_at=final_start + timedelta(minutes=1),
    )
    validate_bar(final, inst)
    assert missing_bar_starts([final], inst, final.start, final.end) == ()
    after_close = bar(
        day,
        frequency="1m",
        start=final.end,
        end=final.end + timedelta(minutes=1),
        available_at=final.end + timedelta(minutes=1),
    )
    with pytest.raises(DomainError):
        validate_bar(after_close, inst)
