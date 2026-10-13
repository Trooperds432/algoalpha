"""Load the synthetic XNYS dataset twice and demonstrate point-in-time visibility."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from quant_terminal.core.clock import SimulationClock
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.feed import DataFeed
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.data.storage import DatasetStore

SOURCE = Path(__file__).parent / "data" / "synthetic_xnys_2026.csv"
INSTRUMENT = InstrumentMeta(
    InstrumentId("ETF-1"), "TEST", "USD", "etf", "XNYS", "America/New_York",
    Decimal("0.01"), Decimal("1"),
)


def main() -> None:
    hashes: list[str] = []
    for _ in range(2):
        with TemporaryDirectory() as root:
            store = DatasetStore(Path(root))
            snapshot = store.import_csv(
                SOURCE, instrument=INSTRUMENT, source="synthetic-fixture",
                publication_delay=timedelta(minutes=1),
            )
            hashes.append(snapshot.content_hash)
            clock = SimulationClock(datetime(2026, 11, 27, 18, 0, tzinfo=UTC))
            feed = DataFeed(store, snapshot, clock)
            assert len(feed.history(end=clock.now, count=10)) == 1
            clock.advance(datetime(2026, 11, 27, 18, 1, tzinfo=UTC))
            assert len(feed.history(end=clock.now, count=10)) == 2
    assert hashes[0] == hashes[1]
    print(f"rows=2 reproducible_content_hash={hashes[0]} publication_gate=passed")


if __name__ == "__main__":
    main()
