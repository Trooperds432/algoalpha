"""Immutable walk-forward window, selection and OOS record types."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from quant_terminal.analytics.metrics import Metric
from quant_terminal.backtesting.models import BacktestResult
from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import DatasetIdentity, ExperimentManifest
from quant_terminal.experiments.models import canonical_json


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class WindowMode(StrEnum):
    ROLLING = "rolling"
    EXPANDING = "expanding"


@dataclass(frozen=True, slots=True)
class WalkForwardSpec:
    walk_forward_id: str
    identity: Mapping[str, object]
    datasets: tuple[DatasetIdentity, ...]
    strategy_source_hash: str
    start: datetime
    end: datetime
    training_duration: timedelta
    test_duration: timedelta
    step: timedelta
    mode: WindowMode

    def __post_init__(self) -> None:
        if not isinstance(self.walk_forward_id, str) or not self.walk_forward_id.strip():
            raise DomainError("walk_forward_id must be nonempty")
        if not isinstance(self.identity, Mapping) or not self.identity:
            raise DomainError("Immutable data and execution identity is required")
        if any(type(key) is not str for key in self.identity):
            raise DomainError("Walk-forward identity keys must be strings")
        frozen_identity = _freeze(json.loads(canonical_json(self.identity)))
        if not isinstance(frozen_identity, Mapping):
            raise DomainError("Walk-forward identity must be a mapping")
        object.__setattr__(self, "identity", frozen_identity)
        datasets = tuple(self.datasets)
        if not datasets or any(not isinstance(x, DatasetIdentity) for x in datasets):
            raise DomainError("Typed immutable M2 dataset identities are required")
        if len({x.snapshot_id for x in datasets}) != len(datasets):
            raise DomainError("Walk-forward dataset IDs must be unique")
        object.__setattr__(self, "datasets", datasets)
        if re.fullmatch(r"[0-9a-fA-F]{64}", self.strategy_source_hash) is None:
            raise DomainError("strategy_source_hash must be SHA-256")
        object.__setattr__(self, "strategy_source_hash", self.strategy_source_hash.lower())
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        if self.end <= self.start:
            raise DomainError("Walk-forward end must follow start")
        for name, value in (
            ("training_duration", self.training_duration),
            ("test_duration", self.test_duration),
            ("step", self.step),
        ):
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise DomainError(f"{name} must be a positive timedelta")
        if self.step != self.test_duration:
            raise DomainError("step must equal test_duration to prevent OOS overlaps or gaps")
        if not isinstance(self.mode, WindowMode):
            raise DomainError("Walk-forward mode must be rolling or expanding")
        if self.start + self.training_duration >= self.end:
            raise DomainError("Interval must contain at least one full IS and one OOS window")

    @property
    def fingerprint(self) -> str:
        from quant_terminal.experiments.models import canonical_hash

        def microseconds(value: timedelta) -> int:
            return ((value.days * 86400 + value.seconds) * 1_000_000) + value.microseconds

        return canonical_hash(
            {
                "walk_forward_id": self.walk_forward_id,
                "identity": self.identity,
                "datasets": [dataset.to_dict() for dataset in self.datasets],
                "strategy_source_hash": self.strategy_source_hash,
                "start": self.start,
                "end": self.end,
                "training_duration_microseconds": microseconds(self.training_duration),
                "test_duration_microseconds": microseconds(self.test_duration),
                "step_microseconds": microseconds(self.step),
                "mode": self.mode.value,
            }
        )


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    index: int
    train_start: datetime
    train_end: datetime
    oos_start: datetime
    oos_end: datetime

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise DomainError("Window index must be nonnegative")
        for name in ("train_start", "train_end", "oos_start", "oos_end"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if not self.train_start < self.train_end == self.oos_start < self.oos_end:
            raise DomainError("Window intervals must be positive and IS must end at OOS start")


@dataclass(frozen=True, slots=True)
class TrainingContext:
    """Only the IS capability is passed into an optimizer callback."""

    index: int
    start: datetime
    end: datetime
    datasets: tuple[DatasetIdentity, ...]
    spec_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        if self.end <= self.start:
            raise DomainError("Training context interval must be positive")
        if re.fullmatch(r"[0-9a-f]{64}", self.spec_fingerprint) is None:
            raise DomainError("Training context requires walk-forward spec fingerprint")


@dataclass(frozen=True, slots=True)
class FrozenSelection:
    campaign_id: str
    campaign_fingerprint: str
    selected_manifest: ExperimentManifest

    def __post_init__(self) -> None:
        if not isinstance(self.campaign_id, str) or not self.campaign_id.strip():
            raise DomainError("M9 campaign identity is required")
        if re.fullmatch(r"[0-9a-fA-F]{64}", self.campaign_fingerprint) is None:
            raise DomainError("M9 campaign fingerprint must be SHA-256")
        if not isinstance(self.selected_manifest, ExperimentManifest):
            raise DomainError("A verified M8 selected-run manifest is required")

    @property
    def parameters(self) -> Mapping[str, object]:
        return self.selected_manifest.config.parameters

    @property
    def metrics(self) -> Mapping[str, Metric]:
        return MappingProxyType(dict(self.selected_manifest.metrics))


@dataclass(frozen=True, slots=True)
class OOSRun:
    manifest: ExperimentManifest
    result_fingerprint: str
    result: BacktestResult

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, ExperimentManifest) or not isinstance(
            self.result, BacktestResult
        ):
            raise DomainError("OOS run requires a typed M8 manifest and M5 result")
        if re.fullmatch(r"[0-9a-fA-F]{64}", self.result_fingerprint) is None:
            raise DomainError("OOS result fingerprint must be SHA-256")
        object.__setattr__(self, "result_fingerprint", self.result_fingerprint.lower())

    @property
    def metrics(self) -> Mapping[str, Metric]:
        return MappingProxyType(dict(self.manifest.metrics))


@dataclass(frozen=True, slots=True)
class MetricDelta:
    metric: str
    in_sample: Decimal | None
    out_of_sample: Decimal | None
    delta: Decimal | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class WalkForwardWindowResult:
    window: WalkForwardWindow
    selection: FrozenSelection
    oos_run: OOSRun
    metric_deltas: tuple[MetricDelta, ...]


@dataclass(frozen=True, slots=True)
class StitchedEquityPoint:
    timestamp: datetime
    equity: Decimal
    window_index: int
    out_of_sample_run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", utc(self.timestamp))
        if not self.equity.is_finite() or self.equity <= 0:
            raise DomainError("Stitched equity must remain positive and finite")
