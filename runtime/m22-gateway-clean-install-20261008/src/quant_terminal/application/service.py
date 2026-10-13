"""One application boundary shared by HTTP, CLI and trusted background workers."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.experiments import canonical_json

from .campaigns import ResearchCampaigns
from .runtime import ResearchRuntime


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(type(k) is not str for k in value):
        raise DomainError("Expected a JSON object")
    return dict(value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError("Expected nonempty text")
    return value


class ResearchService:
    def __init__(self, root: Path, project_root: Path) -> None:
        self.runtime = ResearchRuntime(root, project_root)
        self.campaigns = ResearchCampaigns(self.runtime)

    def execute(
        self,
        kind: str,
        payload: Mapping[str, object],
        job_id: str,
        record: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        if kind == "backtest":
            config = self.runtime.prepare(payload)
            manifest, _ = self.runtime.run(config, "run-" + job_id)
            record({"event": "registered_run", "run_id": manifest.run_id.value})
            return manifest.to_dict()
        operations = {
            "optimize": self.campaigns.optimize,
            "walk-forward": self.campaigns.walk_forward,
            "monte-carlo": self.campaigns.monte_carlo,
            "robustness": self.campaigns.robustness,
        }
        if kind not in operations:
            raise DomainError("Unsupported research operation")
        return operations[kind](payload, job_id, record)

    def import_data(self, payload: Mapping[str, object]) -> dict[str, object]:
        if set(payload) != {"csv", "instrument", "source", "publication_delay_seconds"}:
            raise DomainError(
                "Data import requires csv, instrument, source, publication_delay_seconds"
            )
        data = _object(payload["instrument"])
        fields = {
            "instrument_id",
            "symbol",
            "currency",
            "asset_class",
            "calendar",
            "timezone",
            "tick_size",
            "quantity_step",
        }
        if set(data) != fields:
            raise DomainError("Instrument metadata must contain exactly the documented fields")
        delay = payload["publication_delay_seconds"]
        if type(delay) is not int or not 0 <= delay <= 86400:
            raise DomainError("Publication delay must be an integer in [0,86400] seconds")
        csv = _text(payload["csv"])
        if len(csv.encode("utf-8")) > 8_000_000:
            raise DomainError("CSV import exceeds 8 MB")
        try:
            instrument = InstrumentMeta(
                InstrumentId(_text(data["instrument_id"])),
                _text(data["symbol"]),
                _text(data["currency"]),
                _text(data["asset_class"]),
                _text(data["calendar"]),
                _text(data["timezone"]),
                Decimal(_text(data["tick_size"])),
                Decimal(_text(data["quantity_step"])),
            )
        except ArithmeticError as exc:
            raise DomainError("Invalid instrument Decimal") from exc
        with tempfile.TemporaryDirectory(prefix="quant-import-") as directory:
            path = Path(directory) / "bars.csv"
            path.write_text(csv, encoding="utf-8")
            snapshot = self.runtime.data.import_csv(
                path,
                instrument=instrument,
                source=_text(payload["source"]),
                publication_delay=timedelta(seconds=delay),
            )
        return {
            "snapshot_id": snapshot.snapshot_id,
            "row_count": snapshot.row_count,
            "content_hash": snapshot.content_hash,
            "instrument_id": instrument.instrument_id.value,
        }

    def runs(self) -> list[object]:
        return [manifest.to_dict() for manifest in self.runtime.registry.search()]

    def run_section(self, run_id: str, section: str | None = None) -> object:
        manifest = self.runtime.registry.get(run_id)
        if section is None:
            return manifest.to_dict()
        if section == "artifacts":
            return [asdict(item) for item in manifest.artifacts]
        if section == "metrics":
            return {name: asdict(metric) for name, metric in manifest.metrics}
        report = self.runtime.read_report(run_id)
        if section == "report":
            return report
        table_name = "nav" if section == "equity" else section
        tables = cast(list[dict[str, object]], report["tables"])
        for table in tables:
            if table["name"] == table_name:
                return table
        raise DomainError("Unsupported run section")

    def artifact(self, run_id: str, name: str) -> bytes:
        return self.runtime.registry.read_artifact(run_id, name)

    def html_report(self, run_id: str) -> bytes:
        bundle = self.artifact(run_id, "report-bundle.zip")
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            return archive.read("report.html")

    def compare(self, run_ids: list[str]) -> dict[str, object]:
        if not 2 <= len(run_ids) <= 20:
            raise DomainError("Compare requires between two and twenty run IDs")
        manifests = [self.runtime.registry.get(identity) for identity in run_ids]
        metrics = self.runtime.registry.compare(run_ids)
        warnings = []
        if len({m.config.currency for m in manifests}) > 1:
            warnings.append("Different currencies: monetary metrics are not directly comparable")
        if len({(m.config.start, m.config.end) for m in manifests}) > 1:
            warnings.append("Different sample intervals: metrics are not matched-period estimates")
        if len({canonical_json(m.config.analytics_settings) for m in manifests}) > 1:
            warnings.append("Different analytics conventions")
        return cast(
            dict[str, object],
            json.loads(
                canonical_json(
                    {
                        "run_ids": run_ids,
                        "warnings": warnings,
                        "metrics": {
                            name: {
                                identity: None if metric is None else asdict(metric)
                                for identity, metric in values.items()
                            }
                            for name, values in metrics.items()
                        },
                    }
                )
            ),
        )
