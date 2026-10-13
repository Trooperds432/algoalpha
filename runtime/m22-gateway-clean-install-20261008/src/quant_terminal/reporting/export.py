"""Deterministic HTML, JSON, CSV, and Parquet report bundle export."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quant_terminal.core.exceptions import DomainError
from quant_terminal.reporting.html import render_html
from quant_terminal.reporting.models import BacktestReport, ReportTable, ReportValue


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    return value


def _report_payload(report: BacktestReport) -> dict[str, Any]:
    return {
        "schema_version": report.report_version,
        "strategy_name": report.strategy_name,
        "currency": report.currency,
        "start": _json_value(report.start),
        "end": _json_value(report.end),
        "metadata": dict(report.metadata),
        "analytics_config": {
            "annualization": report.config.annualization,
            "annual_risk_free_rate": str(report.config.annual_risk_free_rate),
            "annual_mar": str(report.config.annual_mar),
            "var_confidences": [str(value) for value in report.config.var_confidences],
            "risk_of_ruin": (
                _json_value(asdict(report.config.risk_of_ruin))
                if report.config.risk_of_ruin is not None
                else None
            ),
        },
        "assumptions": dict(report.assumptions),
        "metrics": [
            {
                "name": name,
                "value": _json_value(metric.value),
                "unit": metric.unit,
                "observations": metric.observations,
                "status": metric.status,
                "reason": metric.reason,
            }
            for name, metric in report.metrics
        ],
        "tables": [
            {
                "name": table.name,
                "columns": list(table.columns),
                "rows": [[_json_value(value) for value in row] for row in table.rows],
            }
            for table in report.tables
        ],
        "charts": [
            {
                "name": chart.name,
                "title": chart.title,
                "kind": chart.kind,
                "x_label": chart.x_label,
                "y_label": chart.y_label,
                "note": chart.note,
                "series": [
                    {
                        "name": series.name,
                        "points": [[label, _json_value(value)] for label, value in series.points],
                    }
                    for series in chart.series
                ],
            }
            for chart in report.charts
        ],
    }


def _csv_value(value: ReportValue) -> str | int | float | bool:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
    return value


def _write_csv(path: Path, table: ReportTable) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(table.columns)
        writer.writerows([_csv_value(value) for value in row] for row in table.rows)


def _timestamp_column(name: str) -> bool:
    return name == "timestamp" or name.endswith("_timestamp") or name.endswith("_at")


def _arrow_type(name: str) -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    if _timestamp_column(name):
        return pa.timestamp("us", tz="UTC")
    if name == "price_return_only":
        return pa.bool_()
    if name in (
        "observations",
        "trade_number",
        "episode",
        "peak_to_recovery_sessions",
        "point_order",
    ):
        return pa.int64()
    # Decimal values stay lossless UTF-8 strings across Arrow implementations.
    return pa.string()


def _arrow_value(name: str, value: ReportValue) -> Any:
    if value is None:
        return None
    if _timestamp_column(name):
        from datetime import UTC

        if not isinstance(value, str):
            raise DomainError(f"Timestamp field {name} must contain ISO text")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    if isinstance(value, (Decimal, bool, int)):
        return str(value) if isinstance(value, Decimal) else value
    return value


def _write_parquet(path: Path, table: ReportTable) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    schema = pa.schema(
        [pa.field(column, _arrow_type(column)) for index, column in enumerate(table.columns)],
        metadata={
            b"schema_version": b"1",
            b"table_name": table.name.encode(),
            b"decimal_encoding": b"UTF-8 exact decimal text",
        },
    )
    data = {
        column: [_arrow_value(column, row[index]) for row in table.rows]
        for index, column in enumerate(table.columns)
    }
    pq.write_table(pa.Table.from_pydict(data, schema=schema), path, compression="zstd")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def export_bundle(report: BacktestReport, destination: str | Path) -> Path:
    """Atomically create one portable report bundle at a new destination path."""
    if not isinstance(report, BacktestReport):
        raise DomainError("BacktestReport is required")
    target = Path(destination).expanduser().absolute()
    if target.exists():
        raise DomainError("Report bundle destination already exists")
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=parent))
    try:
        tables_dir = staging / "tables"
        tables_dir.mkdir()
        _write_json(staging / "report.json", _report_payload(report))
        (staging / "report.html").write_text(render_html(report), encoding="utf-8")
        for table in report.tables:
            _write_csv(tables_dir / f"{table.name}.csv", table)
            _write_parquet(tables_dir / f"{table.name}.parquet", table)

        artifacts = []
        for path in sorted(p for p in staging.rglob("*") if p.is_file()):
            content = path.read_bytes()
            table_name = path.stem if path.parent == tables_dir else ""
            table_for_path = next((item for item in report.tables if item.name == table_name), None)
            artifacts.append(
                {
                    "path": path.relative_to(staging).as_posix(),
                    "format": path.suffix.lstrip("."),
                    "rows": len(table_for_path.rows) if table_for_path is not None else None,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        _write_json(
            staging / "bundle.json",
            {
                "schema_version": "1",
                "strategy_name": report.strategy_name,
                "currency": report.currency,
                "artifacts": artifacts,
                "manifest_note": "This manifest indexes and hashes every artifact except itself.",
            },
        )
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target
