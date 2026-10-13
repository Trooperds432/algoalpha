"""Real file and DuckDB checks for immutable historical data snapshots."""

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.data.storage import BAR_SCHEMA, DatasetStore


def _instrument() -> InstrumentMeta:
    return InstrumentMeta(
        instrument_id=InstrumentId("ETF-1"),
        symbol="TEST",
        currency="USD",
        asset_class="etf",
        calendar="XNYS",
        timezone="America/New_York",
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("1"),
    )


def _rows() -> list[dict[str, str]]:
    return [
        dict(
            instrument_id="ETF-1",
            frequency="1d",
            start="2026-11-25T14:30:00+00:00",
            end="2026-11-25T21:00:00+00:00",
            available_at="2026-11-25T21:01:00+00:00",
            open="100.01",
            high="101.25",
            low="99.98",
            close="100.25",
            volume="1000",
            source="synthetic-fixture",
        ),
        dict(
            instrument_id="ETF-1",
            frequency="1d",
            start="2026-11-27T14:30:00+00:00",
            end="2026-11-27T18:00:00+00:00",
            available_at="2026-11-27T18:01:00+00:00",
            open="100.25",
            high="100.75",
            low="100.00",
            close="100.50",
            volume="500",
            source="synthetic-fixture",
        ),
    ]


def _csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_rows()[0]))
        writer.writeheader()
        writer.writerows(rows)


def _parquet(path: Path, rows: list[dict[str, str]]) -> None:
    arrays: dict[str, pa.Array] = {}
    for field in BAR_SCHEMA:
        values: list[object] = [row[field.name] for row in rows]
        if pa.types.is_timestamp(field.type):
            values = [datetime.fromisoformat(str(value)) for value in values]
        elif pa.types.is_decimal(field.type):
            values = [Decimal(str(value)) for value in values]
        arrays[field.name] = pa.array(values, type=field.type)
    pq.write_table(pa.Table.from_arrays(list(arrays.values()), schema=BAR_SCHEMA), path)


def test_csv_and_parquet_have_same_canonical_content_and_query_exactly(tmp_path: Path) -> None:
    csv_path = tmp_path / "bars.csv"
    parquet_path = tmp_path / "bars.parquet"
    _csv(csv_path, _rows())
    _parquet(parquet_path, _rows())
    store = DatasetStore(tmp_path / "store")
    delay = timedelta(minutes=1)
    csv_snapshot = store.import_csv(
        csv_path, instrument=_instrument(), source="synthetic-fixture", publication_delay=delay
    )
    parquet_snapshot = store.import_parquet(
        parquet_path, instrument=_instrument(), source="synthetic-fixture", publication_delay=delay
    )
    assert csv_snapshot.content_hash == parquet_snapshot.content_hash
    assert csv_snapshot.input_hash != parquet_snapshot.input_hash
    assert csv_snapshot.snapshot_id != parquet_snapshot.snapshot_id
    assert csv_snapshot.row_count == 2
    manifest = json.loads(csv_snapshot.manifest_path.read_text(encoding="utf-8"))
    assert "python-holidays/" in manifest["calendar_version"]
    assert "/tzdata/" in manifest["calendar_version"]
    loaded = store.load_snapshot(csv_snapshot.snapshot_id)
    bars = store.query_bars(loaded, end=datetime(2026, 11, 28, tzinfo=UTC))
    assert len(bars) == 2
    assert bars[0].close == Decimal("100.25")
    assert isinstance(bars[0].close, Decimal)
    assert bars[1].end == datetime(2026, 11, 27, 18, tzinfo=UTC)
    assert store.query_bars(loaded, end=bars[1].start) == (bars[0],)


@pytest.mark.parametrize(
    "bad_rows",
    [
        lambda rows: [rows[0], rows[0]],
        lambda rows: list(reversed(rows)),
        lambda rows: [{**rows[0], "high": "99.99"}, rows[1]],
        lambda rows: [{**rows[0], "volume": "-1"}, rows[1]],
        lambda rows: [{**rows[0], "close": "100.0000000000001"}, rows[1]],
    ],
)
def test_bad_import_is_rejected_without_snapshot(tmp_path: Path, bad_rows: object) -> None:
    csv_path = tmp_path / "bad.csv"
    _csv(csv_path, bad_rows(_rows()))  # type: ignore[operator]
    store = DatasetStore(tmp_path / "store")
    with pytest.raises(DomainError):
        store.import_csv(csv_path, instrument=_instrument(), source="synthetic-fixture")
    assert not list((tmp_path / "store").glob("*/manifest.json"))


def test_snapshots_are_immutable_and_tampering_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    _csv(path, _rows())
    store = DatasetStore(tmp_path / "store")
    snapshot = store.import_csv(path, instrument=_instrument(), source="synthetic-fixture")
    with pytest.raises(DomainError):
        store.import_csv(path, instrument=_instrument(), source="synthetic-fixture")
    with snapshot.parquet_path.open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(DomainError):
        store.load_snapshot(snapshot.snapshot_id)


def test_rewritten_parquet_with_refreshed_file_hash_cannot_keep_identity(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    _csv(path, _rows())
    store = DatasetStore(tmp_path / "store")
    snapshot = store.import_csv(path, instrument=_instrument(), source="synthetic-fixture")
    changed_rows = _rows()
    changed_rows[0]["close"] = "100.26"
    _parquet(snapshot.parquet_path, changed_rows)
    manifest = json.loads(snapshot.manifest_path.read_text(encoding="utf-8"))
    manifest["parquet_sha256"] = hashlib.sha256(snapshot.parquet_path.read_bytes()).hexdigest()
    snapshot.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DomainError, match="content hash"):
        store.load_snapshot(snapshot.snapshot_id)


def test_full_decimal128_precision_survives_csv_and_parquet(tmp_path: Path) -> None:
    rows = _rows()[:1]
    rows[0]["open"] = "1234567890123456789012345.123456789011"
    rows[0]["low"] = rows[0]["open"]
    rows[0]["close"] = "1234567890123456789012345.123456789012"
    rows[0]["high"] = "1234567890123456789012345.123456789013"
    csv_path = tmp_path / "precise.csv"
    parquet_path = tmp_path / "precise.parquet"
    _csv(csv_path, rows)
    _parquet(parquet_path, rows)
    store = DatasetStore(tmp_path / "store")
    csv_snapshot = store.import_csv(csv_path, instrument=_instrument(), source="synthetic-fixture")
    parquet_snapshot = store.import_parquet(
        parquet_path, instrument=_instrument(), source="synthetic-fixture"
    )
    assert csv_snapshot.content_hash == parquet_snapshot.content_hash
    (bar,) = store.query_bars(csv_snapshot, end=datetime(2026, 11, 26, tzinfo=UTC))
    assert bar.close == Decimal("1234567890123456789012345.123456789012")


@pytest.mark.parametrize("kwargs", [{"source": None}, {"publication_delay": "soon"}])
def test_invalid_import_configuration_raises_domain_error(tmp_path: Path, kwargs: dict) -> None:
    path = tmp_path / "bars.csv"
    _csv(path, _rows())
    with pytest.raises(DomainError):
        DatasetStore(tmp_path / "store").import_csv(
            path, instrument=_instrument(), **({"source": "synthetic-fixture"} | kwargs)
        )
