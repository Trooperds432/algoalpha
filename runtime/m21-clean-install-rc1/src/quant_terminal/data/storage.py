"""Immutable, checked historical bar snapshots for internal research queries.

``query_bars`` is deliberately unrestricted by simulation time. Strategies must use
the separate clock-bound DataFeed capability, never a DatasetStore directly.
"""

import csv
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from uuid import uuid4

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.core.serialization import parse_decimal
from quant_terminal.data.calendar import missing_bar_starts, validate_bar
from quant_terminal.data.model import Bar, InstrumentMeta

PriceMode = Literal["raw", "adjusted", "total_return"]
DECIMAL_TYPE = pa.decimal128(38, 12)
TIMESTAMP_TYPE = pa.timestamp("us", tz="UTC")
BAR_SCHEMA = pa.schema(
    [
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("frequency", pa.string(), nullable=False),
        pa.field("start", TIMESTAMP_TYPE, nullable=False),
        pa.field("end", TIMESTAMP_TYPE, nullable=False),
        pa.field("available_at", TIMESTAMP_TYPE, nullable=False),
        *(
            pa.field(name, DECIMAL_TYPE, nullable=False)
            for name in ("open", "high", "low", "close", "volume")
        ),
        pa.field("source", pa.string(), nullable=False),
    ]
)
CSV_COLUMNS = tuple(field.name for field in BAR_SCHEMA)
_MANIFEST_FIELDS = frozenset(
    {
        "snapshot_id",
        "schema_version",
        "calendar_version",
        "pyarrow_version",
        "duckdb_version",
        "instrument",
        "source",
        "price_mode",
        "publication_delay_us",
        "input_hash",
        "content_hash",
        "parquet_sha256",
        "row_count",
        "first_start",
        "last_end",
        "missing_bar_starts",
        "price_convention",
        "bar_schema",
    }
)


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    snapshot_id: str
    parquet_path: Path
    manifest_path: Path
    instrument: InstrumentMeta
    row_count: int
    content_hash: str
    input_hash: str
    source: str
    price_mode: PriceMode
    publication_delay: timedelta


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: object) -> Decimal:
    if not isinstance(value, (str, Decimal)):
        raise DomainError("Decimal fields must be strings or exact Decimal values")
    result = parse_decimal(str(value))
    if result.is_zero():
        return result
    digits = result.as_tuple().digits
    exponent = result.as_tuple().exponent
    if not isinstance(exponent, int):
        raise DomainError("Decimal exponent must be finite")
    shift = exponent + 12
    if shift < 0:
        extra = -shift
        if extra > len(digits) or any(digit != 0 for digit in digits[-extra:]):
            raise DomainError("Decimal exceeds decimal128(38,12) without rounding")
        digit_count = len(digits) - extra
    else:
        digit_count = len(digits) + shift
    if digit_count > 38:
        raise DomainError("Decimal exceeds decimal128(38,12) without rounding")
    return result


def _timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        return utc(parsed)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DomainError("Timestamp must be ISO8601 and timezone-aware") from exc


def _instrument_payload(instrument: InstrumentMeta) -> dict[str, str]:
    return {
        "instrument_id": instrument.instrument_id.value,
        "symbol": instrument.symbol,
        "currency": instrument.currency,
        "asset_class": instrument.asset_class,
        "calendar": instrument.calendar,
        "timezone": instrument.timezone,
        "tick_size": str(instrument.tick_size),
        "quantity_step": str(instrument.quantity_step),
    }


def _instrument_from_payload(data: object) -> InstrumentMeta:
    if not isinstance(data, dict) or set(data) != set(_instrument_payload_keys()):
        raise DomainError("Invalid instrument manifest")
    try:
        return InstrumentMeta(
            instrument_id=InstrumentId(data["instrument_id"]),
            symbol=data["symbol"],
            currency=data["currency"],
            asset_class=data["asset_class"],
            calendar=data["calendar"],
            timezone=data["timezone"],
            tick_size=parse_decimal(data["tick_size"]),
            quantity_step=parse_decimal(data["quantity_step"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError("Invalid instrument manifest") from exc


def _instrument_payload_keys() -> tuple[str, ...]:
    return (
        "instrument_id",
        "symbol",
        "currency",
        "asset_class",
        "calendar",
        "timezone",
        "tick_size",
        "quantity_step",
    )


def _bar_from_row(row: dict[str, object]) -> Bar:
    if set(row) != set(CSV_COLUMNS):
        raise DomainError("Bar columns must match the explicit schema")
    try:
        return Bar(
            instrument_id=InstrumentId(row["instrument_id"]),  # type: ignore[arg-type]
            frequency=row["frequency"],  # type: ignore[arg-type]
            start=_timestamp(row["start"]),
            end=_timestamp(row["end"]),
            available_at=_timestamp(row["available_at"]),
            open=_decimal(row["open"]),
            high=_decimal(row["high"]),
            low=_decimal(row["low"]),
            close=_decimal(row["close"]),
            volume=_decimal(row["volume"]),
            source=row["source"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError("Invalid bar row") from exc


def _canonical_bar(bar: Bar) -> dict[str, str]:
    return {
        "instrument_id": bar.instrument_id.value,
        "frequency": bar.frequency,
        "start": utc(bar.start).isoformat(),
        "end": utc(bar.end).isoformat(),
        "available_at": utc(bar.available_at).isoformat(),
        **{
            name: _canonical_decimal(getattr(bar, name))
            for name in ("open", "high", "low", "close", "volume")
        },
        "source": bar.source,
    }


def _canonical_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _arrow_table(bars: tuple[Bar, ...]) -> pa.Table:
    columns: list[pa.Array] = []
    for field in BAR_SCHEMA:
        if field.name == "instrument_id":
            values: list[object] = [bar.instrument_id.value for bar in bars]
        else:
            values = [getattr(bar, field.name) for bar in bars]
        columns.append(pa.array(values, type=field.type))
    return pa.Table.from_arrays(columns, schema=BAR_SCHEMA)


class DatasetStore:
    """Content-addressed bar files; callers keep strategy access behind DataFeed."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def import_csv(
        self,
        path: Path,
        *,
        instrument: InstrumentMeta,
        source: str,
        publication_delay: timedelta = timedelta(0),
        price_mode: PriceMode = "raw",
    ) -> DatasetSnapshot:
        path = Path(path)
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
                    raise DomainError("CSV columns/order must match BAR_SCHEMA")
                rows = list(reader)
        except (UnicodeError, csv.Error) as exc:
            raise DomainError("Invalid CSV encoding or structure") from exc
        return self._import_rows(
            rows,
            path,
            instrument=instrument,
            source=source,
            publication_delay=publication_delay,
            price_mode=price_mode,
        )

    def import_parquet(
        self,
        path: Path,
        *,
        instrument: InstrumentMeta,
        source: str,
        publication_delay: timedelta = timedelta(0),
        price_mode: PriceMode = "raw",
    ) -> DatasetSnapshot:
        path = Path(path)
        try:
            table = pq.read_table(path)
            if not table.schema.equals(BAR_SCHEMA, check_metadata=False):
                raise DomainError("Parquet schema must exactly match BAR_SCHEMA")
            rows = table.to_pylist()
        except (pa.ArrowException, OSError) as exc:
            raise DomainError("Invalid Parquet input") from exc
        return self._import_rows(
            rows,
            path,
            instrument=instrument,
            source=source,
            publication_delay=publication_delay,
            price_mode=price_mode,
        )

    def _import_rows(
        self,
        rows: list[dict[str, object]],
        path: Path,
        *,
        instrument: InstrumentMeta,
        source: str,
        publication_delay: timedelta,
        price_mode: PriceMode,
    ) -> DatasetSnapshot:
        if not rows:
            raise DomainError("A dataset must contain at least one bar")
        if (
            not isinstance(source, str)
            or not source.strip()
            or price_mode not in ("raw", "adjusted", "total_return")
        ):
            raise DomainError("Source and price mode are required")
        if (
            not isinstance(publication_delay, timedelta)
            or publication_delay < timedelta(0)
            or publication_delay.microseconds != 0
        ):
            # Timedelta's microseconds component is nonzero for subsecond delays.
            raise DomainError("Publication delay must be nonnegative whole seconds")
        bars = tuple(_bar_from_row(row) for row in rows)
        frequency = bars[0].frequency
        previous: datetime | None = None
        for bar in bars:
            if bar.instrument_id != instrument.instrument_id or bar.frequency != frequency:
                raise DomainError("A snapshot contains one instrument and frequency")
            if bar.source != source:
                raise DomainError("Bar source does not match snapshot source")
            if bar.available_at < bar.end + publication_delay:
                raise DomainError("Bar is available before declared publication delay")
            validate_bar(bar, instrument)
            if previous is not None and bar.start <= previous:
                raise DomainError("Duplicate or out-of-order bar start")
            previous = bar.start
        canonical = b"\n".join(_json_bytes(_canonical_bar(bar)) for bar in bars) + b"\n"
        content_hash = _sha256(canonical)
        input_hash = _file_hash(path)
        missing = missing_bar_starts(bars, instrument, bars[0].start, bars[-1].end)
        manifest: dict[str, object] = {
            "schema_version": "bars/1",
            "calendar_version": (
                f"XNYS/python-holidays/{version('holidays')}/tzdata/{version('tzdata')}"
            ),
            "pyarrow_version": pa.__version__,
            "duckdb_version": duckdb.__version__,
            "instrument": _instrument_payload(instrument),
            "source": source,
            "price_mode": price_mode,
            "publication_delay_us": publication_delay // timedelta(microseconds=1),
            "input_hash": input_hash,
            "content_hash": content_hash,
            "row_count": len(bars),
            "first_start": bars[0].start.isoformat(),
            "last_end": bars[-1].end.isoformat(),
            "missing_bar_starts": [value.isoformat() for value in missing],
            "price_convention": price_mode,
            "bar_schema": (
                "instrument_id:string,frequency:string,"
                "start/end/available_at:timestamp[us,UTC],"
                "open/high/low/close/volume:decimal128(38,12),source:string"
            ),
        }
        # Input hashes distinguish independently sourced but economically equal snapshots.
        snapshot_id = _sha256(_json_bytes(manifest))
        target = self.root / snapshot_id
        if target.exists():
            raise DomainError(
                "Identical snapshot already exists; immutable data is not overwritten"
            )
        stage = self.root / (".staging-" + uuid4().hex)
        stage.mkdir()
        try:
            parquet_path = stage / "bars.parquet"
            pq.write_table(_arrow_table(bars), parquet_path, compression="zstd")
            manifest["parquet_sha256"] = _file_hash(parquet_path)
            manifest["snapshot_id"] = snapshot_id
            manifest_path = stage / "manifest.json"
            with manifest_path.open("wb") as handle:
                handle.write(_json_bytes(manifest) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            with parquet_path.open("rb+") as handle:
                os.fsync(handle.fileno())
            if target.exists():
                raise DomainError("Identical snapshot already exists")
            stage.rename(target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return self.load_snapshot(snapshot_id)

    def load_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        if len(snapshot_id) != 64 or any(c not in "0123456789abcdef" for c in snapshot_id):
            raise DomainError("Invalid snapshot ID")
        directory = self.root / snapshot_id
        manifest_path = directory / "manifest.json"
        parquet_path = directory / "bars.parquet"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DomainError("Missing or invalid snapshot manifest") from exc
        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
            raise DomainError("Invalid snapshot manifest fields")
        if manifest["snapshot_id"] != snapshot_id:
            raise DomainError("Snapshot ID mismatch")
        payload = {
            key: value
            for key, value in manifest.items()
            if key not in ("snapshot_id", "parquet_sha256")
        }
        if _sha256(_json_bytes(payload)) != snapshot_id:
            raise DomainError("Snapshot manifest checksum mismatch")
        if not parquet_path.is_file() or _file_hash(parquet_path) != manifest["parquet_sha256"]:
            raise DomainError("Snapshot Parquet checksum mismatch")
        instrument = _instrument_from_payload(manifest["instrument"])
        try:
            delay = timedelta(microseconds=manifest["publication_delay_us"])
            row_count = manifest["row_count"]
            if not isinstance(row_count, int) or row_count <= 0:
                raise ValueError("row count")
            if manifest["price_mode"] not in ("raw", "adjusted", "total_return"):
                raise ValueError("price mode")
        except (TypeError, ValueError, OverflowError) as exc:
            raise DomainError("Invalid snapshot manifest values") from exc
        try:
            table = pq.read_table(parquet_path)
        except (pa.ArrowException, OSError) as exc:
            raise DomainError("Invalid snapshot Parquet data") from exc
        if not table.schema.equals(BAR_SCHEMA, check_metadata=False):
            raise DomainError("Snapshot Parquet schema mismatch")
        bars = tuple(_bar_from_row(row) for row in table.to_pylist())
        if len(bars) != row_count:
            raise DomainError("Snapshot row count mismatch")
        previous: datetime | None = None
        for bar in bars:
            if bar.instrument_id != instrument.instrument_id or bar.source != manifest["source"]:
                raise DomainError("Snapshot bar metadata mismatch")
            if bar.available_at < bar.end + delay:
                raise DomainError("Snapshot publication delay mismatch")
            validate_bar(bar, instrument)
            if previous is not None and bar.start <= previous:
                raise DomainError("Snapshot contains duplicate or out-of-order bars")
            previous = bar.start
        canonical = b"\n".join(_json_bytes(_canonical_bar(bar)) for bar in bars) + b"\n"
        if _sha256(canonical) != manifest["content_hash"]:
            raise DomainError("Snapshot canonical content hash mismatch")
        return DatasetSnapshot(
            snapshot_id=snapshot_id,
            parquet_path=parquet_path,
            manifest_path=manifest_path,
            instrument=instrument,
            row_count=row_count,
            content_hash=manifest["content_hash"],
            input_hash=manifest["input_hash"],
            source=manifest["source"],
            price_mode=manifest["price_mode"],
            publication_delay=delay,
        )

    def query_bars(
        self,
        snapshot: DatasetSnapshot,
        *,
        start: datetime | None = None,
        end: datetime,
    ) -> tuple[Bar, ...]:
        # Re-resolve by ID rather than trust caller-supplied filesystem paths.
        checked = self.load_snapshot(snapshot.snapshot_id)
        if checked != snapshot:
            raise DomainError("Snapshot object does not match immutable manifest")
        upper = utc(end)
        lower = utc(start) if start is not None else None
        if lower is not None and lower > upper:
            raise DomainError("Query start is after end")
        sql = (
            'SELECT instrument_id, frequency, start, "end", available_at, '
            "open, high, low, close, volume, source FROM read_parquet(?) WHERE start < ?"
        )
        params: list[object] = [str(checked.parquet_path), upper]
        if lower is not None:
            sql += " AND start >= ?"
            params.append(lower)
        sql += " ORDER BY start"
        try:
            with duckdb.connect(database=":memory:", read_only=False) as connection:
                cursor = connection.execute(sql, params)
                names = [column[0] for column in cursor.description]
                rows = [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]
        except duckdb.Error as exc:
            raise DomainError("Unable to query immutable Parquet snapshot") from exc
        bars = tuple(_bar_from_row(row) for row in rows)
        for bar in bars:
            validate_bar(bar, checked.instrument)
        return bars
