"""Reproducible, local synthetic performance baseline; never submits orders.

Run: .venv/Scripts/python.exe benchmarks/m15_baseline.py \
     --bars 780 --output benchmarks/baseline.json
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import platform
import sys
import tempfile
import tracemalloc
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_terminal.analytics.metrics import Metric  # noqa: E402
from quant_terminal.backtesting.engine import BacktestEngine  # noqa: E402
from quant_terminal.backtesting.models import BacktestConfig  # noqa: E402
from quant_terminal.core.identifiers import InstrumentId  # noqa: E402
from quant_terminal.data.calendar import _session_bounds  # noqa: E402
from quant_terminal.data.model import InstrumentMeta  # noqa: E402
from quant_terminal.data.storage import CSV_COLUMNS, DatasetStore  # noqa: E402
from quant_terminal.execution.models import ExecutionConfig, PerUnitCommission  # noqa: E402
from quant_terminal.optimization import (  # noqa: E402
    Direction,
    OptimizationSpec,
    ParameterDimension,
    SearchMethod,
    TrialEvaluation,
    run_optimization,
)

NY = ZoneInfo("America/New_York")
INSTRUMENT = InstrumentMeta(
    InstrumentId("M15-SYNTH"),
    "M15",
    "USD",
    "etf",
    "XNYS",
    "America/New_York",
    Decimal("0.01"),
    Decimal("1"),
)


class HoldCash:
    name = "m15_hold_cash"

    def on_bar(self, context: object, bar: object) -> tuple[()]:
        return ()


def write_fixture(path: Path, count: int) -> tuple[datetime, datetime, str]:
    """Write contiguous scheduled XNYS minutes with fixed, exact OHLCV values."""
    if count < 1:
        raise ValueError("--bars must be positive")
    remaining = count
    day = datetime(2026, 1, 5, 12, tzinfo=NY)
    first: datetime | None = None
    last: datetime | None = None
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        while remaining:
            bounds = _session_bounds(day)
            if bounds is not None:
                opening, closing = bounds
                minute = opening
                while minute < closing and remaining:
                    end = minute + timedelta(minutes=1)
                    writer.writerow(
                        {
                            "instrument_id": INSTRUMENT.instrument_id.value,
                            "frequency": "1m",
                            "start": minute.isoformat(),
                            "end": end.isoformat(),
                            "available_at": end.isoformat(),
                            "open": "100.00",
                            "high": "100.01",
                            "low": "99.99",
                            "close": "100.00",
                            "volume": "1000",
                            "source": "m15-synthetic",
                        }
                    )
                    first = first or minute
                    last = end
                    minute = end
                    remaining -= 1
            day += timedelta(days=1)
    assert first is not None and last is not None
    return first, last, hashlib.sha256(path.read_bytes()).hexdigest()


def process_memory() -> tuple[int | None, int | None]:
    """Return current and process-lifetime peak working set on Windows."""
    if sys.platform != "win32":
        return None, None

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set", ctypes.c_size_t),
            ("working_set", ctypes.c_size_t),
            ("quota_peak_paged_pool", ctypes.c_size_t),
            ("quota_paged_pool", ctypes.c_size_t),
            ("quota_peak_nonpaged_pool", ctypes.c_size_t),
            ("quota_nonpaged_pool", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
            ("private_usage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Counters),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.working_set, counters.peak_working_set


def measured(operation: object) -> tuple[object, float, int, tuple[int | None, int | None]]:
    """Return result, wall seconds, Python peak, and process-memory checkpoint."""
    tracemalloc.start()
    start = perf_counter()
    try:
        result = operation()  # type: ignore[operator]
        elapsed = perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        memory = process_memory()
    finally:
        tracemalloc.stop()
    return result, elapsed, peak, memory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=780)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.bars < 1:
        parser.error("--bars must be positive")
    with tempfile.TemporaryDirectory(prefix="algoalpha-m15-") as temporary:
        base = Path(temporary)
        start, end, source_sha256 = write_fixture(base / "bars.csv", args.bars)
        store = DatasetStore(base / "store")
        snapshot, import_s, import_peak, import_process = measured(
            lambda: store.import_csv(
                base / "bars.csv",
                instrument=INSTRUMENT,
                source="m15-synthetic",
            )
        )
        queried, query_s, query_peak, query_process = measured(
            lambda: store.query_bars(snapshot, start=start, end=end)
        )
        assert len(queried) == args.bars
        engine = BacktestEngine(
            store,
            (snapshot,),
            HoldCash(),
            BacktestConfig(Decimal("100000"), "USD"),
            ExecutionConfig(commission=PerUnitCommission(Decimal("0.01"))),
        )
        run_result, backtest_s, backtest_peak, backtest_process = measured(
            lambda: engine.run(start=start, end=end)
        )
        assert len(run_result.nav) == args.bars + 1
        assert all(point.nav == Decimal("100000") for point in run_result.nav)
        spec = OptimizationSpec(
            "m15-synthetic",
            {"dataset_sha256": source_sha256},
            (ParameterDimension("window", (5, 10, 20)),),
            SearchMethod.GRID,
            3,
            17,
            "sharpe",
            Direction.MAXIMIZE,
        )
        recorded: list[object] = []

        def evaluate(index: int, parameters: object) -> TrialEvaluation:
            result = engine.run(start=start, end=end)
            assert result.nav[-1].nav == Decimal("100000")
            return TrialEvaluation(
                {"sharpe": Metric(Decimal("0"), "ratio", args.bars)},
                f"m15-{index}",
                source_sha256,
                source_sha256,
            )

        campaign, optimization_s, optimization_peak, optimization_process = measured(
            lambda: run_optimization(spec, evaluate, recorded.append)
        )
        assert campaign.completed and len(campaign.trials) == 3 and len(recorded) == 3
        payload = {
            "schema_version": 2,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "processor": platform.processor(),
                "machine": platform.machine(),
            },
            "fixture": {
                "kind": "deterministic-flat-price-XNYS-1m",
                "bars": args.bars,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "csv_sha256": source_sha256,
                "snapshot_content_hash": snapshot.content_hash,
            },
            "measurements": {
                "csv_import_seconds": import_s,
                "csv_import_python_peak_bytes": import_peak,
                "csv_import_working_set_checkpoint_bytes": import_process[0],
                "csv_import_process_peak_working_set_bytes": import_process[1],
                "query_seconds": query_s,
                "query_python_peak_bytes": query_peak,
                "query_working_set_checkpoint_bytes": query_process[0],
                "query_process_peak_working_set_bytes": query_process[1],
                "backtest_seconds": backtest_s,
                "backtest_python_peak_bytes": backtest_peak,
                "backtest_working_set_checkpoint_bytes": backtest_process[0],
                "backtest_process_peak_working_set_bytes": backtest_process[1],
                "event_bars_per_second": args.bars / backtest_s,
                "optimization_seconds": optimization_s,
                "optimization_python_peak_bytes": optimization_peak,
                "optimization_working_set_checkpoint_bytes": optimization_process[0],
                "optimization_process_peak_working_set_bytes": optimization_process[1],
                "optimization_trials_per_second": 3 / optimization_s,
            },
            "scope": "single-process in-memory campaign; no M8 durable writes or UI/API overhead",
        }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
