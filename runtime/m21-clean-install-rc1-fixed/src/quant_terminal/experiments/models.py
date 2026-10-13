"""Immutable experiment identities and context-independent canonical fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Self

from quant_terminal.analytics.metrics import Metric
from quant_terminal.backtesting.models import BacktestResult
from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import Identifier, RunId
from quant_terminal.orders.models import Fill, OrderSnapshot


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{name} must be a nonempty string")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise DomainError(f"{name} must be a SHA-256 hex digest")
    return value.lower()


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DomainError(f"{name} must be an integer >= {minimum}")
    return value


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise DomainError("Canonical Decimal must be finite")
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _freeze(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise DomainError("Canonical mapping keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    raise DomainError(f"Unsupported canonical value type: {type(value).__name__}")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise DomainError("A string-keyed mapping is required")
    return {key: item for key, item in value.items()}


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, (tuple, list)):
        raise DomainError("An array is required")
    return tuple(value)


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, Identifier):
        return value.value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in _mapping(value).items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    raise DomainError(f"Unsupported canonical value type: {type(value).__name__}")


def canonical_json(value: object) -> bytes:
    """Sorted UTF-8 JSON; floats, nonfinite Decimals and naive times fail closed."""
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    snapshot_id: str
    content_hash: str
    input_hash: str
    row_count: int
    instrument_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "content_hash", "input_hash"):
            object.__setattr__(self, name, _hash(getattr(self, name), name))
        _integer(self.row_count, "row_count", 1)
        identities = tuple(_text(x, "instrument_id") for x in _sequence(self.instrument_ids))
        if not identities or len(set(identities)) != len(identities):
            raise DomainError("Instrument identities must be nonempty and unique")
        object.__setattr__(self, "instrument_ids", identities)

    def to_dict(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        data = _mapping(value)
        _keys(data, cls)
        return cls(
            _text(data["snapshot_id"], "snapshot_id"),
            _text(data["content_hash"], "content_hash"),
            _text(data["input_hash"], "input_hash"),
            _integer(data["row_count"], "row_count", 1),
            tuple(_text(x, "instrument_id") for x in _sequence(data["instrument_ids"])),
        )


def _keys(data: Mapping[str, object], model: type[object]) -> None:
    if set(data) != set(model.__dataclass_fields__):  # type: ignore[attr-defined]
        raise DomainError(f"Unexpected {model.__name__} fields")


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    strategy_id: str
    strategy_name: str
    strategy_source_hash: str
    project_source_hash: str
    git_commit: str | None
    engine_version: str
    dependency_lock_hash: str
    datasets: tuple[DatasetIdentity, ...]
    start: datetime
    end: datetime
    currency: str
    initial_capital: Decimal
    parameters: Mapping[str, object]
    seed: int
    backtest_settings: Mapping[str, object]
    analytics_settings: Mapping[str, object]
    benchmark: Mapping[str, object] | None
    execution_assumptions: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in ("strategy_id", "strategy_name", "engine_version"):
            _text(getattr(self, name), name)
        for name in ("strategy_source_hash", "project_source_hash", "dependency_lock_hash"):
            object.__setattr__(self, name, _hash(getattr(self, name), name))
        if self.git_commit is not None:
            if (
                not isinstance(self.git_commit, str)
                or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", self.git_commit) is None
            ):
                raise DomainError("git_commit must be a full revision hash or explicit null")
            object.__setattr__(self, "git_commit", self.git_commit.lower())
        datasets = _sequence(self.datasets)
        if not datasets or any(not isinstance(item, DatasetIdentity) for item in datasets):
            raise DomainError("At least one typed DatasetIdentity is required")
        object.__setattr__(self, "datasets", datasets)
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        if self.end <= self.start:
            raise DomainError("Experiment end must follow start")
        if not isinstance(self.currency, str) or re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise DomainError("currency must be an uppercase three-letter code")
        if (
            not isinstance(self.initial_capital, Decimal)
            or not self.initial_capital.is_finite()
            or self.initial_capital <= 0
        ):
            raise DomainError("initial_capital must be a positive finite Decimal")
        if _integer(self.seed, "seed") >= 2**64:
            raise DomainError("seed must be unsigned 64-bit")
        for name in (
            "parameters",
            "backtest_settings",
            "analytics_settings",
            "execution_assumptions",
        ):
            object.__setattr__(self, name, _freeze(_mapping(getattr(self, name))))
        if self.benchmark is not None:
            object.__setattr__(self, "benchmark", _freeze(_mapping(self.benchmark)))

    def to_dict(self) -> dict[str, object]:
        data = {field.name: getattr(self, field.name) for field in fields(self)}
        data["datasets"] = [item.to_dict() for item in self.datasets]
        return dict(_mapping(_json_value(data)))

    @property
    def input_fingerprint(self) -> str:
        return canonical_hash(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> Self:
        data = _mapping(value)
        _keys(data, cls)
        try:
            return cls(
                strategy_id=_text(data["strategy_id"], "strategy_id"),
                strategy_name=_text(data["strategy_name"], "strategy_name"),
                strategy_source_hash=_text(data["strategy_source_hash"], "strategy_source_hash"),
                project_source_hash=_text(data["project_source_hash"], "project_source_hash"),
                git_commit=None
                if data["git_commit"] is None
                else _text(data["git_commit"], "git_commit"),
                engine_version=_text(data["engine_version"], "engine_version"),
                dependency_lock_hash=_text(data["dependency_lock_hash"], "dependency_lock_hash"),
                datasets=tuple(DatasetIdentity.from_dict(x) for x in _sequence(data["datasets"])),
                start=datetime.fromisoformat(_text(data["start"], "start")),
                end=datetime.fromisoformat(_text(data["end"], "end")),
                currency=_text(data["currency"], "currency"),
                initial_capital=Decimal(_text(data["initial_capital"], "initial_capital")),
                parameters=_mapping(data["parameters"]),
                seed=_integer(data["seed"], "seed"),
                backtest_settings=_mapping(data["backtest_settings"]),
                analytics_settings=_mapping(data["analytics_settings"]),
                benchmark=None if data["benchmark"] is None else _mapping(data["benchmark"]),
                execution_assumptions=_mapping(data["execution_assumptions"]),
            )
        except (ValueError, ArithmeticError) as exc:
            raise DomainError("Invalid experiment configuration") from exc


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    name: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.name) is None
        ):
            raise DomainError("Artifact name must be a safe single path component")
        if self.name.endswith(".") or self.name.split(".")[0].upper() in (
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{x}" for x in range(1, 10)),
            *(f"LPT{x}" for x in range(1, 10)),
        ):
            raise DomainError("Reserved artifact name")
        object.__setattr__(self, "sha256", _hash(self.sha256, "artifact sha256"))
        _integer(self.size_bytes, "size_bytes")

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "sha256": self.sha256, "size_bytes": self.size_bytes}


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    run_id: RunId
    created_at: datetime
    config: ExperimentConfig
    result_fingerprint: str
    metrics: tuple[tuple[str, Metric], ...]
    python_version: str
    runtime_metadata: Mapping[str, object]
    artifacts: tuple[ArtifactDescriptor, ...] = ()
    tags: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId) or not isinstance(self.config, ExperimentConfig):
            raise DomainError("Typed run ID and experiment configuration are required")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise DomainError("Unsupported experiment schema version")
        object.__setattr__(self, "created_at", utc(self.created_at))
        object.__setattr__(
            self, "result_fingerprint", _hash(self.result_fingerprint, "result fingerprint")
        )
        _text(self.python_version, "python_version")
        object.__setattr__(self, "runtime_metadata", _freeze(_mapping(self.runtime_metadata)))
        metrics: list[tuple[str, Metric]] = []
        for pair in _sequence(self.metrics):
            items = _sequence(pair)
            if len(items) != 2 or not isinstance(items[1], Metric):
                raise DomainError("Metrics must be named typed Metric records")
            name, metric = _text(items[0], "metric name"), items[1]
            if metric.value is not None and (
                not isinstance(metric.value, Decimal) or not metric.value.is_finite()
            ):
                raise DomainError("Metric value must be a finite Decimal or null")
            _text(metric.unit, "metric unit")
            _text(metric.status, "metric status")
            _integer(metric.observations, "metric observations")
            if metric.reason is not None:
                _text(metric.reason, "metric reason")
            if metric.value is None and metric.reason is None:
                raise DomainError("Unavailable metrics require a reason")
            metrics.append((name, metric))
        if len({name for name, _ in metrics}) != len(metrics):
            raise DomainError("Metric names must be unique")
        object.__setattr__(self, "metrics", tuple(sorted(metrics)))
        artifacts = _sequence(self.artifacts)
        if any(not isinstance(item, ArtifactDescriptor) for item in artifacts):
            raise DomainError("Artifacts must be typed descriptors")
        if len({item.name.casefold() for item in self.artifacts}) != len(artifacts):
            raise DomainError("Artifact names must be unique ignoring case")
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        tags = tuple(_text(tag, "tag") for tag in _sequence(self.tags))
        object.__setattr__(self, "tags", tuple(sorted(set(tags))))

    @property
    def input_fingerprint(self) -> str:
        return canonical_hash(
            {
                "configuration": self.config.to_dict(),
                "python_version": self.python_version,
            }
        )

    def to_dict(self) -> dict[str, object]:
        return dict(
            _mapping(
                _json_value(
                    {
                        "schema_version": self.schema_version,
                        "run_id": self.run_id.value,
                        "created_at": self.created_at,
                        "config": self.config.to_dict(),
                        "input_fingerprint": self.input_fingerprint,
                        "result_fingerprint": self.result_fingerprint,
                        "metrics": {
                            name: {f.name: getattr(metric, f.name) for f in fields(metric)}
                            for name, metric in self.metrics
                        },
                        "python_version": self.python_version,
                        "runtime_metadata": self.runtime_metadata,
                        "artifacts": [item.to_dict() for item in self.artifacts],
                        "tags": self.tags,
                    }
                )
            )
        )

    @classmethod
    def from_dict(cls, value: object) -> Self:
        data = dict(_mapping(value))
        fingerprint = _hash(data.pop("input_fingerprint", None), "input fingerprint")
        _keys(data, cls)
        try:
            metrics: list[tuple[str, Metric]] = []
            for name, raw in _mapping(data["metrics"]).items():
                item = _mapping(raw)
                _keys(item, Metric)
                metrics.append(
                    (
                        name,
                        Metric(
                            None
                            if item["value"] is None
                            else Decimal(_text(item["value"], "metric value")),
                            _text(item["unit"], "metric unit"),
                            _integer(item["observations"], "observations"),
                            _text(item["status"], "metric status"),
                            None if item["reason"] is None else _text(item["reason"], "reason"),
                        ),
                    )
                )
            artifacts: list[ArtifactDescriptor] = []
            for raw in _sequence(data["artifacts"]):
                item = _mapping(raw)
                _keys(item, ArtifactDescriptor)
                artifacts.append(
                    ArtifactDescriptor(
                        _text(item["name"], "artifact name"),
                        _text(item["sha256"], "artifact hash"),
                        _integer(item["size_bytes"], "artifact size"),
                    )
                )
            manifest = cls(
                RunId(_text(data["run_id"], "run_id")),
                datetime.fromisoformat(_text(data["created_at"], "created_at")),
                ExperimentConfig.from_dict(data["config"]),
                _text(data["result_fingerprint"], "result fingerprint"),
                tuple(metrics),
                _text(data["python_version"], "python_version"),
                _mapping(data["runtime_metadata"]),
                tuple(artifacts),
                tuple(_text(x, "tag") for x in _sequence(data["tags"])),
                _integer(data["schema_version"], "schema_version"),
            )
        except (ValueError, ArithmeticError) as exc:
            raise DomainError("Invalid experiment manifest") from exc
        if manifest.input_fingerprint != fingerprint:
            raise DomainError("Input fingerprint does not match configuration")
        return manifest


def _fill(fill: Fill) -> dict[str, object]:
    data = {
        field.name: getattr(fill, field.name)
        for field in fields(fill)
        if field.name != "instrument"
    }
    data["instrument"] = {
        field.name: getattr(fill.instrument, field.name) for field in fields(fill.instrument)
    }
    return data


def _order(order: OrderSnapshot) -> dict[str, object]:
    intent = {
        field.name: getattr(order.intent, field.name)
        for field in fields(order.intent)
        if field.name != "instrument"
    }
    intent["instrument"] = {
        field.name: getattr(order.intent.instrument, field.name)
        for field in fields(order.intent.instrument)
    }
    return {
        "intent": intent,
        "status": order.status,
        "accepted_at": order.accepted_at,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "events": [
            {f.name: getattr(event, f.name) for f in fields(event)} for event in order.events
        ],
        "fills": [_fill(fill) for fill in order.fills],
        "reason": order.reason,
        "trade": None
        if order.trade is None
        else {f.name: getattr(order.trade, f.name) for f in fields(order.trade)},
    }


def economic_result_projection(result: BacktestResult) -> dict[str, object]:
    """Explicit economic output schema; labels and runtime metadata are excluded."""
    if not isinstance(result, BacktestResult):
        raise DomainError("A BacktestResult is required")
    nav: list[dict[str, object]] = []
    for point in result.nav:
        item = {f.name: getattr(point, f.name) for f in fields(point) if f.name != "positions"}
        item["positions"] = [
            {f.name: getattr(position, f.name) for f in fields(position) if f.name != "symbol"}
            for position in point.positions
        ]
        nav.append(item)
    return dict(
        _mapping(
            _json_value(
                {
                    "schema_version": 1,
                    "start": result.start,
                    "end": result.end,
                    "nav": nav,
                    "orders": [_order(order) for order in result.orders],
                    "fills": [_fill(fill) for fill in result.fills],
                    "trades": [
                        {f.name: getattr(trade, f.name) for f in fields(trade)}
                        for trade in result.trades
                    ],
                    "benchmark": [
                        {f.name: getattr(point, f.name) for f in fields(point)}
                        for point in result.benchmark
                    ],
                    "commission_cost": result.commission_cost,
                    "slippage_cost": result.slippage_cost,
                }
            )
        )
    )


def result_fingerprint(result: BacktestResult) -> str:
    return canonical_hash(economic_result_projection(result))


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    """Explicit identity attestation returned by a trusted rerun adapter."""

    result: BacktestResult
    strategy_source_hash: str
    project_source_hash: str
    datasets: tuple[DatasetIdentity, ...]
    input_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.result, BacktestResult):
            raise DomainError("Replay outcome requires a BacktestResult")
        for name in ("strategy_source_hash", "project_source_hash", "input_fingerprint"):
            object.__setattr__(self, name, _hash(getattr(self, name), name))
        datasets = _sequence(self.datasets)
        if not datasets or any(not isinstance(value, DatasetIdentity) for value in datasets):
            raise DomainError("Replay outcome requires typed dataset identities")
        object.__setattr__(self, "datasets", tuple(datasets))
