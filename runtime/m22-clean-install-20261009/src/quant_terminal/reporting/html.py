"""Dependency-free, escaped HTML and static SVG report rendering."""

from __future__ import annotations

from decimal import Decimal, localcontext
from html import escape

from quant_terminal.reporting.models import (
    BacktestReport,
    Chart,
    ReportTable,
    ReportValue,
)
from quant_terminal.reporting.utils import decimal_context, iso_utc

_COLORS = ("#175CD3", "#039855", "#D92D20", "#7A5AF8", "#DC6803", "#0E7090")
_SECTIONS = {
    "Summary": ("equity", "log_equity"),
    "Performance": (
        "monthly_returns",
        "annual_returns",
        "rolling_volatility",
        "rolling_sharpe",
        "return_distribution",
    ),
    "Risk": ("underwater", "drawdown_duration"),
    "Trading Statistics": ("trade_pnl_distribution", "mae_mfe"),
    "Drawdowns": (),
    "Execution Costs": (),
    "Exposure": ("exposure", "leverage", "turnover"),
    "Benchmark Comparison": ("benchmark",),
    "Trades": (),
    "Orders": (),
    "Fills": (),
}


def _label(value: ReportValue) -> str:
    return "" if value is None else str(value)


def _svg(chart: Chart) -> str:
    width, height = 900, 310
    left, right, top, bottom = 74, 18, 24, 58
    plot_width, plot_height = width - left - right, height - top - bottom
    all_points = [point for series in chart.series for point in series.points]
    if not all_points or not any(value is not None for _, value in all_points):
        message = chart.note or "No observations are available."
        return (
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(chart.title)}">'
            f"<title>{escape(chart.title)}</title>"
            f'<text x="{left}" y="150">{escape(message)}</text></svg>'
        )

    with localcontext(decimal_context()):
        yvalues = [value for _, value in all_points if value is not None]
        ymin, ymax = min(yvalues), max(yvalues)
        if ymin == ymax:
            padding = abs(ymin) / Decimal(10) if ymin else Decimal(1)
            ymin, ymax = ymin - padding, ymax + padding
        if chart.kind == "bar" and ymin > 0:
            ymin = Decimal(0)
        if chart.kind == "bar" and ymax < 0:
            ymax = Decimal(0)
        if chart.kind == "scatter":
            xvalues = [Decimal(x) for x, value in all_points if value is not None]
            xmin, xmax = min(xvalues), max(xvalues)
            if xmin == xmax:
                padding = abs(xmin) / Decimal(10) if xmin else Decimal(1)
                xmin, xmax = xmin - padding, xmax + padding
            xkeys: list[str] = []
        else:
            xmin, xmax = Decimal(0), Decimal(1)
            xkeys = list(dict.fromkeys(x for x, _ in all_points))

        def ycoord(value: Decimal) -> float:
            ratio = (value - ymin) / (ymax - ymin)
            return top + plot_height * (1 - float(ratio))

        def xcoord(key: str, index: int, count: int) -> float:
            if chart.kind == "scatter":
                ratio = (Decimal(key) - xmin) / (xmax - xmin)
                return left + plot_width * float(ratio)
            return left + plot_width * index / max(count - 1, 1)

        svg = [
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(chart.title)}">',
            f"<title>{escape(chart.title)}</title>",
            f'<rect x="{left}" y="{top}" width="{plot_width}" '
            f'height="{plot_height}" fill="#fff" stroke="#D0D5DD"/>',
        ]
        for tick in range(5):
            ratio = Decimal(tick) / Decimal(4)
            value = ymax - ratio * (ymax - ymin)
            y = top + plot_height * tick / 4
            svg.append(
                f'<path d="M {left} {y:.1f} H {left + plot_width}" stroke="#EAECF0"/>'
                f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">'
                f"{escape(f'{value:.4g}')}</text>"
            )
        if chart.kind == "bar":
            count = max(len(xkeys), 1)
            bar_width = max(2.0, plot_width / count * 0.68)
            zero_y = ycoord(Decimal(0))
            for series_index, series in enumerate(chart.series):
                color = _COLORS[series_index % len(_COLORS)]
                for key, point_value in series.points:
                    if point_value is None or key not in xkeys:
                        continue
                    index = xkeys.index(key)
                    x = xcoord(key, index, count) - bar_width / 2
                    y = ycoord(point_value)
                    svg.append(
                        f'<rect x="{x:.1f}" y="{min(y, zero_y):.1f}" width="{bar_width:.1f}" '
                        f'height="{max(abs(zero_y - y), 1):.1f}" fill="{color}"><title>'
                        f"{escape(series.name)}: {escape(key)} = "
                        f"{escape(str(point_value))}</title></rect>"
                    )
        elif chart.kind == "scatter":
            for series_index, series in enumerate(chart.series):
                color = _COLORS[series_index % len(_COLORS)]
                for key, point_value in series.points:
                    if point_value is None:
                        continue
                    svg.append(
                        f'<circle cx="{xcoord(key, 0, 1):.1f}" '
                        f'cy="{ycoord(point_value):.1f}" r="4" '
                        f'fill="{color}"><title>MAE {escape(key)}, '
                        f"MFE {escape(str(point_value))}</title></circle>"
                    )
        else:
            count = max(len(xkeys), 1)
            for series_index, series in enumerate(chart.series):
                color = _COLORS[series_index % len(_COLORS)]
                paths: list[str] = []
                drawing = False
                for key, point_value in series.points:
                    if point_value is None or key not in xkeys:
                        drawing = False
                        continue
                    x = xcoord(key, xkeys.index(key), count)
                    y = ycoord(point_value)
                    paths.append(("L" if drawing else "M") + f" {x:.1f} {y:.1f}")
                    drawing = True
                svg.append(
                    f'<path d="{" ".join(paths)}" fill="none" stroke="{color}" stroke-width="2"/>'
                )
        if chart.kind != "scatter" and xkeys:
            for index in sorted({0, len(xkeys) // 2, len(xkeys) - 1}):
                x = xcoord(xkeys[index], index, len(xkeys))
                label = xkeys[index][:10] if len(xkeys[index]) > 12 else xkeys[index]
                svg.append(
                    f'<text x="{x:.1f}" y="{height - bottom + 19}" text-anchor="middle">'
                    f"{escape(label)}</text>"
                )
        svg.append(
            f'<text x="{left + plot_width / 2:.1f}" y="{height - 8}" text-anchor="middle">'
            f"{escape(chart.x_label)}</text>"
        )
        if chart.series:
            for index, series in enumerate(chart.series):
                y = 14 + index * 14
                color = _COLORS[index % len(_COLORS)]
                svg.append(
                    f'<rect x="{left}" y="{y - 8}" width="10" height="10" fill="{color}"/>'
                    f'<text x="{left + 15}" y="{y}">{escape(series.name)}</text>'
                )
        svg.append("</svg>")
        return "".join(svg)


def _format(value: ReportValue) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return _label(value)


def _table_html(table: ReportTable, *, preview: int = 100) -> str:
    headers = "".join(f"<th>{escape(column)}</th>" for column in table.columns)
    body = []
    for row in table.rows[:preview]:
        body.append(
            "<tr>" + "".join(f"<td>{escape(_format(value))}</td>" for value in row) + "</tr>"
        )
    omitted = len(table.rows) - min(len(table.rows), preview)
    note = (
        f"<p>Showing {min(len(table.rows), preview)} of {len(table.rows)} rows.</p>"
        if omitted
        else ""
    )
    links = (
        f'<p class="exports"><a href="tables/{escape(table.name)}.csv">Download CSV</a> · '
        f'<a href="tables/{escape(table.name)}.parquet">Download Parquet</a></p>'
    )
    return (
        f"<details><summary>{escape(table.name.replace('_', ' ').title())} "
        f"({len(table.rows)} rows)</summary>"
        f'{links}{note}<div class="table-scroll"><table><thead><tr>{headers}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div></details>"
    )


def render_html(report: BacktestReport) -> str:
    """Render a complete offline HTML report with inline CSS and static SVG."""
    metrics = dict(report.metrics)
    hero = ("total_return", "cagr", "sharpe", "maximum_drawdown")
    cards = []
    for name in hero:
        metric = metrics[name]
        value = "Unavailable" if metric.value is None else f"{metric.value:.4g}"
        cards.append(
            f'<div class="card"><span>{escape(name.replace("_", " ").title())}</span>'
            f"<strong>{escape(value)}</strong><small>{escape(metric.unit)}</small></div>"
        )
    by_chart = {chart.name: chart for chart in report.charts}
    by_table = {table.name: table for table in report.tables}
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{escape(report.strategy_name)} — Backtest Report</title>",
        "<style>body{font:15px system-ui,sans-serif;color:#101828;margin:0;background:#F9FAFB}"
        "main{max-width:1440px;margin:auto;padding:28px}h1,h2{color:#101828}h2{margin-top:38px}"
        ".meta,.note{color:#475467}.cards{display:grid;"
        "grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}"
        ".card,figure,details{background:white;border:1px solid #EAECF0;"
        "border-radius:9px;padding:14px}"
        ".card span,.card small{display:block;color:#667085}"
        ".card strong{display:block;font-size:24px;margin:7px 0}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:16px}"
        "figure{margin:0;overflow:hidden}svg{width:100%;height:auto}"
        "svg text{font:11px system-ui,sans-serif;fill:#475467}"
        "table{border-collapse:collapse;width:100%;font-size:12px}"
        "th,td{padding:7px;border-bottom:1px solid #EAECF0;"
        "text-align:left;white-space:nowrap}"
        "th{background:#F2F4F7;position:sticky;top:0}.table-scroll{overflow:auto;max-height:480px}"
        "details{margin:12px 0}summary{font-weight:650;cursor:pointer}.exports a{margin-right:12px}"
        "@media print{body{background:white}main{max-width:none;padding:0}"
        "figure,details{break-inside:avoid}.grid{display:block}}</style>"
        "</head><body><main>",
        f"<h1>{escape(report.strategy_name)} — Backtest Report</h1>",
        f'<p class="meta">{escape(iso_utc(report.start) or "")} to '
        f"{escape(iso_utc(report.end) or '')} · "
        f"Currency: {escape(report.currency)} · Report schema {escape(report.report_version)}</p>",
        '<section id="summary"><h2>Summary</h2><div class="cards">',
        "".join(cards),
        "</div></section>",
    ]
    assumption_rows = report.assumptions + report.metadata
    parts.append(
        "<details><summary>Run assumptions and metadata</summary><table><tbody>"
        + "".join(
            f"<tr><th>{escape(name)}</th><td>{escape(value)}</td></tr>"
            for name, value in assumption_rows
        )
        + "</tbody></table></details>"
    )
    sections_tables = {
        "Summary": ("nav",),
        "Performance": ("session_returns", "chart_data"),
        "Risk": ("risk_metrics",),
        "Trading Statistics": ("trading_statistics",),
        "Drawdowns": ("drawdowns",),
        "Execution Costs": ("execution_costs",),
        "Exposure": ("exposure_metrics", "exposure", "positions"),
        "Benchmark Comparison": ("benchmark",),
        "Trades": ("trades",),
        "Orders": ("orders",),
        "Fills": ("fills",),
    }
    for section, chart_names in _SECTIONS.items():
        if section == "Summary":
            continue
        section_id = section.lower().replace(" ", "-")
        parts.append(f'<section id="{section_id}"><h2>{escape(section)}</h2>')
        charts_html = []
        for name in chart_names:
            chart = by_chart[name]
            note = f'<p class="note">{escape(chart.note)}</p>' if chart.note else ""
            charts_html.append(
                f"<figure><figcaption>{escape(chart.title)}</figcaption>{_svg(chart)}{note}</figure>"
            )
        if charts_html:
            parts.append('<div class="grid">' + "".join(charts_html) + "</div>")
        for table_name in sections_tables.get(section, ()):
            parts.append(_table_html(by_table[table_name]))
        if section == "Performance":
            # Show every metric's value, units, status, and null reason.
            parts.append(_table_html(by_table["metrics"]))
        parts.append("</section>")
    parts.extend(
        (
            '<footer><p class="meta"><a href="report.json">Download complete JSON report</a>. '
            "Research output. See the CSV/Parquet tables "
            "for complete records. Benchmark values are price-return-only "
            "where present.</p></footer>",
            "</main></body></html>",
        )
    )
    return "".join(parts)
