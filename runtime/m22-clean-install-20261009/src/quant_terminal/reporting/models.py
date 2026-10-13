"""Immutable presentation records, separate from engine-domain objects."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quant_terminal.analytics.metrics import AnalyticsConfig, Metric
from quant_terminal.core.exceptions import DomainError

ReportValue = str | int | bool | Decimal | None


@dataclass(frozen=True, slots=True)
class ReportTable:
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[ReportValue, ...], ...]

    def __post_init__(self) -> None:
        if (
            not self.name
            or any(char not in "abcdefghijklmnopqrstuvwxyz_0123456789" for char in self.name)
            or not self.columns
        ):
            raise DomainError("Report table requires a name and columns")
        if len(set(self.columns)) != len(self.columns):
            raise DomainError("Report table column names must be unique")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise DomainError(f"Rows do not match the {self.name} table schema")
        for row in self.rows:
            for value in row:
                if value is not None and not isinstance(value, (str, int, bool, Decimal)):
                    raise DomainError("Report table values must be scalar JSON-compatible types")
                if isinstance(value, Decimal) and not value.is_finite():
                    raise DomainError("Report table decimals must be finite")


@dataclass(frozen=True, slots=True)
class ChartSeries:
    name: str
    points: tuple[tuple[str, Decimal | None], ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise DomainError("Chart series requires a name")
        if any(value is not None and not value.is_finite() for _, value in self.points):
            raise DomainError("Chart values must be finite Decimals")


@dataclass(frozen=True, slots=True)
class Chart:
    name: str
    title: str
    kind: str
    x_label: str
    y_label: str
    series: tuple[ChartSeries, ...]
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.title or self.kind not in ("line", "bar", "scatter"):
            raise DomainError("Chart name, title, and supported kind are required")


@dataclass(frozen=True, slots=True)
class BacktestReport:
    report_version: str
    strategy_name: str
    currency: str
    start: datetime
    end: datetime
    metadata: tuple[tuple[str, str], ...]
    config: AnalyticsConfig
    assumptions: tuple[tuple[str, str], ...]
    metrics: tuple[tuple[str, Metric], ...]
    tables: tuple[ReportTable, ...]
    charts: tuple[Chart, ...]

    def __post_init__(self) -> None:
        if not self.report_version or self.end <= self.start:
            raise DomainError("Report version and positive report duration are required")
        table_names = [table.name for table in self.tables]
        chart_names = [chart.name for chart in self.charts]
        if len(table_names) != len(set(table_names)) or len(chart_names) != len(set(chart_names)):
            raise DomainError("Report table and chart names must be unique")

    def metric(self, name: str) -> Metric:
        try:
            return dict(self.metrics)[name]
        except KeyError as exc:
            raise DomainError(f"Unknown report metric: {name}") from exc

    def table(self, name: str) -> ReportTable:
        try:
            return next(table for table in self.tables if table.name == name)
        except StopIteration as exc:
            raise DomainError(f"Unknown report table: {name}") from exc

    def chart(self, name: str) -> Chart:
        try:
            return next(chart for chart in self.charts if chart.name == name)
        except StopIteration as exc:
            raise DomainError(f"Unknown report chart: {name}") from exc
