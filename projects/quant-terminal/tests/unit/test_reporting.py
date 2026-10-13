"""Offline report model, escaping and multi-format artifact tests."""

import csv
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from quant_terminal.accounting.ledger import PositionSnapshot
from quant_terminal.backtesting.models import BacktestResult, BenchmarkPoint, NAVPoint
from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import FillId, InstrumentId, OrderId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.orders.models import (
    Fill,
    OrderEvent,
    OrderIntent,
    OrderSnapshot,
    Side,
    Trade,
    TradeType,
)
from quant_terminal.reporting import build_report, export_bundle

START = datetime(2024, 1, 1, tzinfo=UTC)
INSTRUMENT = InstrumentMeta(
    InstrumentId("ETF-1"),
    "=2+2",
    "USD",
    "etf",
    "XNYS",
    "America/New_York",
    Decimal("0.01"),
    Decimal("1"),
)


def _nav(day: int, value: str, position: PositionSnapshot | None = None) -> NAVPoint:
    timestamp = START + timedelta(days=day)
    return NAVPoint(
        timestamp,
        Decimal(value),
        None,
        Decimal(value),
        (position,) if position else (),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        None if Decimal(value) <= 0 else Decimal(0),
        Decimal(0),
    )


def _fill(sequence: int, side: Side, price: str, day: int) -> Fill:
    timestamp = START + timedelta(days=day)
    return Fill(
        FillId(f"F-{sequence}"),
        OrderId(f"O-{sequence}"),
        INSTRUMENT,
        side,
        Decimal("2"),
        Decimal(price),
        Decimal(price),
        Decimal("0.2"),
        timestamp,
        timestamp,
    )


def _result(strategy_name: str = "Strategy <script>alert(1)</script>") -> BacktestResult:
    opened = PositionSnapshot(
        INSTRUMENT.instrument_id,
        INSTRUMENT.symbol,
        Decimal("2"),
        Decimal("100"),
        Decimal("105"),
        Decimal("210"),
        Decimal(0),
        Decimal("10"),
    )
    fills = (_fill(1, Side.BUY, "100", 1), _fill(2, Side.SELL, "110", 2))
    orders = []
    for index, (side, price, fill) in enumerate(
        ((Side.BUY, "100", fills[0]), (Side.SELL, "110", fills[1])), start=1
    ):
        order_id = OrderId(f"O-{index}")
        created = START + timedelta(days=index)
        intent = OrderIntent(
            order_id,
            INSTRUMENT,
            side,
            TradeType.MARKET,
            Decimal("2"),
            created,
            max_execution_price=Decimal(price) if side is Side.BUY else None,
        )
        events = (
            OrderEvent(order_id, 1, OrderStatus.ACCEPTED, created),
            OrderEvent(order_id, 2, OrderStatus.FILLED, created),
        )
        orders.append(
            OrderSnapshot(
                intent,
                OrderStatus.FILLED,
                created,
                Decimal("2"),
                Decimal(0),
                events,
                (fill,),
                None,
                Trade(
                    order_id, side, Decimal("2"), Decimal(price), Decimal("0.2"), (fill.fill_id,)
                ),
            )
        )
    return BacktestResult(
        strategy_name,
        START,
        START + timedelta(days=2),
        (_nav(0, "1000"), _nav(1, "1010", opened), _nav(2, "1019.6")),
        tuple(orders),
        fills,
        (),
        Decimal("0.4"),
        Decimal("0"),
        (
            BenchmarkPoint(
                START + timedelta(days=1), InstrumentId("ETF-2"), Decimal(50), Decimal(1)
            ),
            BenchmarkPoint(
                START + timedelta(days=2), InstrumentId("ETF-2"), Decimal(51), Decimal("1.02")
            ),
        ),
        (
            ("currency", "USD"),
            ("api_key", "never-export-this"),
            ("api-key", "hyphen-secret-value"),
            ("api key", "space-secret-value"),
            ("API KEY ID", "identifier-secret-value"),
            ("secret_key", "joined-secret-value"),
            ("cost_assumption", "configured"),
        ),
    )


def test_report_has_complete_sections_charts_fifo_trades_and_redacted_metadata() -> None:
    report = build_report(_result())

    assert len(report.charts) == 15
    assert report.table("trades").rows[0][12:16] == (
        Decimal("20"),
        Decimal("0.2"),
        Decimal("0.2"),
        Decimal("19.6"),
    )
    assert report.table("trades").rows[0][-2:] == (Decimal(0), Decimal(5))
    assert report.table("fills").rows[0][4] == "buy"
    assert len(report.table("orders").rows) == 2
    order_events = json.loads(report.table("orders").rows[0][-1])
    assert [event["status"] for event in order_events] == ["accepted", "filled"]
    assert report.table("session_returns").rows[-1][1] == Decimal(
        "0.009504950495049504950495049504950"
    )
    assert any(row[0] == "equity" for row in report.table("chart_data").rows)
    assert dict(report.metadata)["api_key"] == "[REDACTED]"
    assert dict(report.metadata)["api-key"] == "[REDACTED]"
    assert dict(report.metadata)["api key"] == "[REDACTED]"
    assert dict(report.metadata)["API KEY ID"] == "[REDACTED]"
    assert len(report.table("drawdowns").rows) == 0
    assert report.chart("benchmark").note is not None
    assert "price-return-only" in report.chart("benchmark").note


def test_bundle_exports_html_json_csv_and_parquet_with_verified_hashes(tmp_path: Path) -> None:
    report = build_report(_result())
    target = export_bundle(report, tmp_path / "complete-report")

    html = (target / "report.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "price-return-only" in html
    assert "total_return" in html
    assert "risk_metrics" in html
    assert "trading_statistics" in html
    assert "http://" not in html and "https://" not in html
    assert "Download Parquet" in html

    payload = json.loads((target / "report.json").read_text(encoding="utf-8"))
    assert payload["tables"]
    assert payload["metadata"]["api_key"] == "[REDACTED]"
    assert payload["metadata"]["api-key"] == "[REDACTED]"
    assert payload["metadata"]["api key"] == "[REDACTED]"
    assert payload["metadata"]["API KEY ID"] == "[REDACTED]"
    for secret_value in (
        "never-export-this",
        "hyphen-secret-value",
        "space-secret-value",
        "identifier-secret-value",
        "joined-secret-value",
    ):
        assert secret_value not in html
        assert secret_value not in json.dumps(payload)
    trade_table = next(table for table in payload["tables"] if table["name"] == "trades")
    assert trade_table["rows"][0][15] == "19.6"
    assert all(
        isinstance(metric["value"], str) or metric["value"] is None for metric in payload["metrics"]
    )

    with (target / "tables" / "fills.csv").open(encoding="utf-8", newline="") as stream:
        fills = list(csv.DictReader(stream))
    assert fills[0]["symbol"] == "'=2+2"
    assert fills[0]["economic_timestamp"].endswith("Z")

    trade_parquet = pq.read_table(target / "tables" / "trades.parquet")
    assert trade_parquet.num_rows == 1
    assert trade_parquet.column("net_pnl").to_pylist() == ["19.6"]
    assert str(trade_parquet.schema.field("entry_timestamp").type) == "timestamp[us, tz=UTC]"
    chart_parquet = pq.read_table(target / "tables" / "chart_data.parquet")
    assert str(chart_parquet.schema.field("point_order").type) == "int64"
    assert chart_parquet.num_rows == len(report.chart("equity").series[0].points) + sum(
        len(series.points)
        for chart in report.charts
        if chart.name != "equity"
        for series in chart.series
    )

    manifest = json.loads((target / "bundle.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        data = (target / artifact["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
        assert len(data) == artifact["bytes"]


def test_export_rejects_existing_destination_without_modifying_it(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(DomainError, match="already exists"):
        export_bundle(build_report(_result()), target)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_nonpositive_nav_is_a_gap_in_log_equity_chart() -> None:
    result = _result()
    nav = result.nav[:-1] + (_nav(2, "-1"),)
    insolvent = BacktestResult(
        result.strategy_name,
        result.start,
        result.end,
        nav,
        result.orders,
        result.fills,
        result.trades,
        result.commission_cost,
        result.slippage_cost,
        result.benchmark,
        result.metadata,
    )

    chart = build_report(insolvent).chart("log_equity")

    assert chart.series[0].points[-1][1] is None
    assert "Nonpositive NAV" in chart.note


def test_monthly_and_annual_charts_compound_returns_and_mark_boundary_periods() -> None:
    year_end = datetime(2024, 12, 30, tzinfo=UTC)
    nav = (
        replace(_nav(0, "100"), timestamp=year_end),
        replace(_nav(1, "110"), timestamp=year_end + timedelta(days=1)),
        replace(_nav(2, "100"), timestamp=year_end + timedelta(days=2)),
    )
    result = BacktestResult(
        "calendar-fixture",
        year_end,
        year_end + timedelta(days=2),
        nav,
        (),
        (),
        (),
        Decimal(0),
        Decimal(0),
        (),
        (("currency", "USD"),),
    )

    report = build_report(result)
    monthly = report.chart("monthly_returns").series[0].points
    annual = report.chart("annual_returns").series[0].points

    assert [label for label, _ in monthly] == ["2024-12*", "2025-01*"]
    assert monthly[0][1] == Decimal("0.1")
    assert monthly[1][1] == Decimal("-0.0909090909090909090909090909090909")
    assert [label for label, _ in annual] == ["2024*", "2025*"]
