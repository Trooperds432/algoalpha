"""The strategy-facing path must not reveal a completed but unpublished bar."""

import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quant_terminal.core.clock import SimulationClock
from quant_terminal.core.exceptions import DomainError, FutureDataError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.feed import DataFeed
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.data.storage import DatasetStore


def test_bar_visibility_changes_only_at_publication(tmp_path: Path) -> None:
    instrument = InstrumentMeta(
        InstrumentId("ETF-1"),
        "TEST",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal("0.01"),
        Decimal("1"),
    )
    csv_path = tmp_path / "known.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "instrument_id",
                "frequency",
                "start",
                "end",
                "available_at",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "source",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "instrument_id": "ETF-1",
                "frequency": "1d",
                "start": "2026-11-25T14:30:00+00:00",
                "end": "2026-11-25T21:00:00+00:00",
                "available_at": "2026-11-25T21:01:00+00:00",
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100.5",
                "volume": "1000",
                "source": "synthetic-fixture",
            }
        )
    store = DatasetStore(tmp_path / "snapshots")
    snapshot = store.import_csv(
        csv_path,
        instrument=instrument,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    clock = SimulationClock(datetime(2026, 11, 25, 21, 0, tzinfo=UTC))
    feed = DataFeed(store, snapshot, clock)
    assert feed.history(end=clock.now, count=1) == ()
    clock.advance(datetime(2026, 11, 25, 21, 0, 59, tzinfo=UTC))
    assert feed.history(end=clock.now, count=1) == ()
    clock.advance(datetime(2026, 11, 25, 21, 1, tzinfo=UTC))
    visible = feed.history(end=clock.now, count=1)
    assert len(visible) == 1 and visible[0].close == Decimal("100.5")
    # A later clock must not expose the bar in a historical query before publication.
    before_publication = datetime(2026, 11, 25, 21, 0, tzinfo=UTC)
    assert feed.history(end=before_publication, count=1) == ()
    assert feed.between(start=datetime(2026, 11, 25, tzinfo=UTC), end=before_publication) == ()
    with pytest.raises(FutureDataError):
        feed.history(end=clock.now + timedelta(microseconds=1), count=1)
    with pytest.raises(FutureDataError):
        feed.between(
            start=datetime(2026, 11, 25, tzinfo=UTC),
            end=clock.now + timedelta(microseconds=1),
        )
    with pytest.raises(DomainError):
        feed.history(end=clock.now, count=0)
