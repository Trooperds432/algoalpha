"""Create labeled synthetic data and headless request examples outside the source tree."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from quant_terminal.application.service import ResearchService
from quant_terminal.data.calendar import _session_bounds
from quant_terminal.experiments import canonical_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = args.state_dir.resolve()
    service = ResearchService(root, project)
    lines = ["instrument_id,frequency,start,end,available_at,open,high,low,close,volume,source"]
    day = datetime(2026, 7, 1, 16, tzinfo=UTC)
    index, prior_close = 0, Decimal(100)
    while day < datetime(2026, 9, 1, tzinfo=UTC):
        session = _session_bounds(day)
        if session:
            opening, closing = session
            price_open = prior_close + Decimal("0.2")
            price_close = Decimal(100) + index * Decimal("0.3") + (index % 8 - 4) * Decimal("0.7")
            lines.append(
                f"ETF-DEMO,1d,{opening.isoformat()},{closing.isoformat()},{closing.isoformat()},"
                f"{price_open},{max(price_open, price_close) + 1},"
                f"{min(price_open, price_close) - 1},{price_close},"
                "100000,synthetic-research-example"
            )
            prior_close = price_close
            index += 1
        day += timedelta(days=1)
    dataset = service.import_data(
        {
            "csv": "\n".join(lines),
            "source": "synthetic-research-example",
            "publication_delay_seconds": 0,
            "instrument": {
                "instrument_id": "ETF-DEMO",
                "symbol": "SYNTHETIC",
                "currency": "USD",
                "asset_class": "etf",
                "calendar": "XNYS",
                "timezone": "America/New_York",
                "tick_size": "0.01",
                "quantity_step": "1",
            },
        }
    )
    backtest = {
        "strategy": "buy_and_hold",
        "parameters": {"instrument_id": "ETF-DEMO", "quantity": "10"},
        "snapshot_ids": [dataset["snapshot_id"]],
        "start": "2026-07-01T00:00:00Z",
        "end": "2026-09-01T00:00:00Z",
        "currency": "USD",
        "initial_capital": "10000",
        "seed": 20260927,
        "execution": {
            "commission_per_unit": "0.1",
            "slippage_kind": "fixed",
            "slippage_value": "0.01",
            "maximum_participation": "0.01",
        },
        "analytics": {"annualization": 252},
        "benchmark": "ETF-DEMO",
    }
    optimize = {
        "backtest": backtest,
        "dimensions": [{"name": "quantity", "choices": ["5", "10", "20"]}],
        "method": "grid",
        "candidate_budget": 3,
        "seed": 20260927,
        "objective_metric": "total_return",
        "direction": "maximize",
        "constraints": [],
    }
    requests = root / "example-requests"
    requests.mkdir(exist_ok=True)
    for name, payload in {
        "backtest": backtest,
        "optimize": optimize,
        "walk-forward": {
            **optimize,
            "training_seconds": 14 * 86400,
            "test_seconds": 7 * 86400,
            "mode": "rolling",
        },
    }.items():
        target = requests / (name + ".json")
        if target.exists() and target.read_bytes() != canonical_json(payload):
            raise FileExistsError(f"Existing request differs: {target}")
        target.write_bytes(canonical_json(payload))
    print(
        canonical_json(
            {
                "dataset": dataset,
                "requests": str(requests),
                "label": "SYNTHETIC DATA ONLY; no strategy approved",
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
