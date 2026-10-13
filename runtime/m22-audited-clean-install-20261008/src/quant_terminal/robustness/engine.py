"""Durably recorded, identity-checked robustness campaigns over trusted M8 runs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import cast

from quant_terminal.core.exceptions import DomainError
from quant_terminal.execution.models import (
    ConfiguredSlippage,
    ExecutionConfig,
    PerUnitCommission,
    SlippageKind,
)
from quant_terminal.experiments import (
    ExperimentConfig,
    ExperimentManifest,
    ExperimentStore,
    IntegrityError,
    canonical_hash,
    canonical_json,
)

from .models import RobustnessScenario, RobustnessSpec, ScenarioKind, compare_scenario_metrics


def execution_config(config: ExperimentConfig) -> ExecutionConfig:
    """Decode the supported exact M4 execution schema; unknown models reject."""
    data = config.execution_assumptions
    commission = data.get("commission")
    slippage = data.get("slippage")
    if not isinstance(commission, Mapping) or commission.get("kind") != "per_unit":
        raise DomainError("Robustness requires the supported per-unit commission model")
    if not isinstance(slippage, Mapping):
        raise DomainError("Explicit slippage model is required")
    minimum = commission.get("minimum")
    return ExecutionConfig(
        maximum_participation=Decimal(str(data["maximum_participation"])),
        commission=PerUnitCommission(
            Decimal(str(commission["per_unit_rate"])),
            None if minimum is None else Decimal(str(minimum)),
        ),
        slippage=ConfiguredSlippage(
            SlippageKind(str(slippage["kind"])), Decimal(str(slippage["value"]))
        ),
        zero_cost_assumption=cast(str | None, data.get("zero_cost_assumption")),
    )


def _assign(data: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    target = data
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            raise DomainError(f"Scenario path is absent from baseline: {path}")
        target = child
    if parts[-1] not in target:
        raise DomainError(f"Scenario field is absent from baseline: {path}")
    target[parts[-1]] = value


def scenario_config(spec: RobustnessSpec, scenario: RobustnessScenario) -> ExperimentConfig:
    """Build the entire expected M8 identity before launching any scenario."""
    if scenario not in spec.scenarios:
        raise DomainError("Scenario is not part of the pinned specification")
    baseline = spec.baseline.config
    if "robustness_overlay" in baseline.backtest_settings:
        raise DomainError("Nested robustness baselines require a new explicit control run")
    data = cast(dict[str, object], json.loads(canonical_json(baseline.to_dict())))
    overlay: dict[str, object] = {
        "campaign_fingerprint": spec.fingerprint,
        "scenario_id": scenario.scenario_id,
        "bar_delay": 0,
        "universe": tuple(i for dataset in baseline.datasets for i in dataset.instrument_ids),
    }
    changes = dict(scenario.changes)
    if scenario.kind is ScenarioKind.BAR_DELAY:
        overlay["bar_delay"] = changes["bar_delay"]
    elif scenario.kind is ScenarioKind.UNIVERSE:
        if set(changes) - {"universe.include", "universe.exclude"}:
            raise DomainError("Universe scenarios support explicit include/exclude only")
        universe = cast(tuple[str, ...], overlay["universe"])
        included = changes.get("universe.include", universe)
        excluded = changes.get("universe.exclude", ())
        if not isinstance(included, (tuple, list)) or not isinstance(excluded, (tuple, list)):
            raise DomainError("Universe include/exclude must be instrument ID arrays")
        if any(not isinstance(i, str) or i not in universe for i in (*included, *excluded)):
            raise DomainError("Universe perturbation requires pinned instrument data")
        overlay["universe"] = tuple(sorted(set(included) - set(excluded)))
        if not overlay["universe"]:
            raise DomainError("Universe perturbation must retain at least one instrument")
    elif scenario.kind is ScenarioKind.REGIME:
        if set(changes) != {"regime.start", "regime.end", "regime.label"}:
            raise DomainError("Regime requires a declared contiguous interval and label")
        data["start"], data["end"] = changes["regime.start"], changes["regime.end"]
        label = changes["regime.label"]
        if not isinstance(label, str) or not label.strip():
            raise DomainError("Regime label must be nonempty")
        overlay["regime_label"] = label
    else:
        for path, value in scenario.changes:
            _assign(data, path, value)
    backtest = cast(dict[str, object], data["backtest_settings"])
    backtest["robustness_overlay"] = overlay
    candidate = ExperimentConfig.from_dict(data)
    if candidate.start < baseline.start or candidate.end > baseline.end:
        raise DomainError("Date perturbations must remain inside the pinned baseline interval")
    execution = execution_config(candidate)
    if scenario.kind is ScenarioKind.COMMISSION:
        original = execution_config(baseline).commission
        assert isinstance(original, PerUnitCommission)
        assert isinstance(execution.commission, PerUnitCommission)
        if execution.commission.per_unit_rate < original.per_unit_rate or (
            (execution.commission.minimum or Decimal(0)) < (original.minimum or Decimal(0))
        ):
            raise DomainError("Commission stress may only increase configured costs")
    if scenario.kind is ScenarioKind.SLIPPAGE:
        original_slippage = execution_config(baseline).slippage
        assert isinstance(original_slippage, ConfiguredSlippage)
        assert isinstance(execution.slippage, ConfiguredSlippage)
        if original_slippage.kind is not SlippageKind.NONE and (
            execution.slippage.kind != original_slippage.kind
            or execution.slippage.value < original_slippage.value
        ):
            raise DomainError("Slippage stress must increase the same configured model")
    execution_data = cast(dict[str, object], data["execution_assumptions"])
    execution_data["commission_model_repr"] = repr(execution.commission)
    execution_data["slippage_model_repr"] = repr(execution.slippage)
    return ExperimentConfig.from_dict(data)


class ScenarioExecutionError(Exception):
    """A trial-local strategy failure that must be recorded before continuing."""


@dataclass(frozen=True, slots=True)
class RobustnessRun:
    specification_fingerprint: str
    rows: tuple[bytes, ...]
    completed: bool

    def artifact_bytes(self) -> bytes:
        return canonical_json(
            {
                "specification_fingerprint": self.specification_fingerprint,
                "completed": self.completed,
                "scenarios": [json.loads(row) for row in self.rows],
            }
        )


def run_robustness(
    spec: RobustnessSpec,
    registry: ExperimentStore,
    evaluator: Callable[[RobustnessScenario, ExperimentConfig], ExperimentManifest],
    recorder: Callable[[bytes], None],
    *,
    cancelled: Callable[[], bool] | None = None,
) -> RobustnessRun:
    """Run preflighted scenarios; every outcome is recorded, integrity errors stop."""
    stored = registry.get(spec.baseline.run_id)
    if stored != spec.baseline:
        raise IntegrityError("Robustness baseline differs from registered M8 identity")
    planned: list[tuple[RobustnessScenario, ExperimentConfig | None, str | None]] = []
    for scenario in spec.scenarios:
        try:
            planned.append((scenario, scenario_config(spec, scenario), None))
        except (DomainError, ValueError, KeyError) as exc:
            planned.append((scenario, None, f"{type(exc).__name__}: {exc}"))
    if not callable(evaluator) or not callable(recorder):
        raise DomainError("Trusted evaluator and durable recorder are required")
    rows: list[bytes] = []
    seen = {spec.baseline.run_id.value}
    for scenario, config, preflight_error in planned:
        if cancelled is not None and cancelled():
            return RobustnessRun(spec.fingerprint, tuple(rows), False)
        row: dict[str, object] = {
            "specification_fingerprint": spec.fingerprint,
            "scenario_id": scenario.scenario_id,
            "kind": scenario.kind.value,
            "changes": scenario.changes,
            "rationale": scenario.rationale,
            "expected_config": None if config is None else config.to_dict(),
            "baseline": spec.baseline.to_dict(),
        }
        if config is None:
            row.update(status="invalid", reason=preflight_error, manifest=None)
            encoded = canonical_json(row)
            recorder(encoded)
            rows.append(encoded)
            continue
        try:
            manifest = evaluator(scenario, config)
            if not isinstance(manifest, ExperimentManifest):
                raise IntegrityError("Scenario adapter must return an M8 manifest")
            if manifest.run_id.value in seen or manifest != registry.get(manifest.run_id):
                raise IntegrityError("Scenario run must be unique and durably registered")
            if canonical_hash(manifest.config.to_dict()) != canonical_hash(config.to_dict()):
                raise IntegrityError("Scenario adapter changed unapproved input fields")
            seen.add(manifest.run_id.value)
            comparison = compare_scenario_metrics(
                scenario,
                dict(spec.baseline.metrics),
                dict(manifest.metrics),
                run_id=manifest.run_id.value,
            )
            row.update(
                status="completed", manifest=manifest.to_dict(), comparison=asdict(comparison)
            )
        except ScenarioExecutionError as exc:
            row.update(status="failed", reason=str(exc), manifest=None)
        except Exception as exc:
            row.update(
                status="integrity_failed", reason=f"{type(exc).__name__}: {exc}", manifest=None
            )
            recorder(canonical_json(row))
            raise
        encoded = canonical_json(row)
        recorder(encoded)
        rows.append(encoded)
    return RobustnessRun(spec.fingerprint, tuple(rows), True)
