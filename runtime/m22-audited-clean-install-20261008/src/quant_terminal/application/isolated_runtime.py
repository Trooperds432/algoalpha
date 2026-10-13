"""Operator-only research integration; public admission remains disabled.

The operator supplies a bounded source package and an owned backend. This class
does not confer acceptance on that backend and is not a public request handler.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from quant_terminal import __version__
from quant_terminal.application.runtime import ResearchRuntime, _integer, _object, _text
from quant_terminal.backtesting.strategies import Strategy
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.experiments import ExperimentConfig, IntegrityError, build_source_bundle
from quant_terminal.sandbox.protocol import (
    MAX_DATA_ROWS,
    ProtocolError,
    SandboxBudget,
    SandboxRequest,
    SandboxResponse,
    decode_request,
    encode_request,
)
from quant_terminal.sandbox.source_package import SourcePackage
from quant_terminal.sandbox.strategy import IsolatedStrategy


class CancellableBackend(Protocol):
    def execute(
        self,
        request_blob: bytes,
        cancel: threading.Event | None = None,
        *,
        expected_identity: str | None = None,
    ) -> SandboxResponse: ...

    def identity(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _BoundBackend:
    backend: CancellableBackend
    cancel: threading.Event | None
    expected_identity: str | None = None

    def execute(self, request_blob: bytes) -> SandboxResponse:
        self.identity()
        response = self.backend.execute(
            request_blob,
            cancel=self.cancel,
            expected_identity=self.expected_identity,
        )
        self.identity()
        return response

    def identity(self) -> str:
        if self.cancel is not None and self.cancel.is_set():
            raise DomainError("Isolated research cancelled")
        identity = self.backend.identity()
        if self.expected_identity is not None and identity != self.expected_identity:
            raise IntegrityError("Isolated backend differs from prepared configuration")
        return identity


class IsolatedResearchRuntime(ResearchRuntime):
    """Use normal as-of, risk, accounting and canonical artifact machinery."""

    def __init__(
        self,
        root: Path,
        project_root: Path,
        backend: CancellableBackend,
        package: SourcePackage,
        *,
        cancel: threading.Event | None = None,
    ) -> None:
        if not isinstance(package, SourcePackage):
            raise DomainError("Validated source package required")
        self.backend = _BoundBackend(backend, cancel)
        self.package = package
        self.cancel = cancel
        super().__init__(root, project_root)

    def _available(self) -> str:
        if self.cancel is not None and self.cancel.is_set():
            raise DomainError("Isolated research cancelled")
        return _text(self.backend.identity())

    def _parameters(
        self, value: object, seed: int
    ) -> tuple[tuple[InstrumentId, ...], int, SandboxBudget, str]:
        params = _object(
            value,
            {"instrument_ids", "history_count", "budget", "backend_identity"},
            {"instrument_ids", "history_count", "budget", "backend_identity"},
        )
        instruments = params["instrument_ids"]
        if not isinstance(instruments, (tuple, list)) or not 1 <= len(instruments) <= 100:
            raise DomainError("Distinct configured isolated instruments required")
        identities = tuple(InstrumentId(_text(item)) for item in instruments)
        if len(set(identities)) != len(identities):
            raise DomainError("Distinct configured isolated instruments required")
        history = _integer(params["history_count"])
        if not 1 <= history <= MAX_DATA_ROWS:
            raise DomainError("Isolated history exceeds protocol budget")
        fields = set(SandboxBudget.__dataclass_fields__)
        raw_budget = _object(params["budget"], fields, fields)
        try:
            budget = SandboxBudget(**{key: _integer(raw_budget[key]) for key in fields})
            # Reuse the pinned wire validator; never evaluate the opaque source.
            budget = decode_request(
                encode_request(
                    SandboxRequest(
                        "configuration-check",
                        self.package.source,
                        self.package.source_sha256,
                        (),
                        hashlib.sha256(b"[]").hexdigest(),
                        (),
                        seed,
                        budget,
                    )
                )
            ).budget
        except ProtocolError as exc:
            raise DomainError("Invalid isolated protocol configuration") from exc
        if budget.scratch_bytes or budget.scratch_files or budget.cpu_seconds > budget.wall_seconds:
            raise DomainError("Isolated backend requires zero scratch and CPU within wall budget")
        return identities, history, budget, _text(params["backend_identity"])

    def prepare(self, payload: Mapping[str, object]) -> ExperimentConfig:
        backend_identity = self._available()
        required = {
            "instrument_ids",
            "budget",
            "snapshot_ids",
            "start",
            "end",
            "currency",
            "initial_capital",
            "execution",
        }
        data = _object(
            payload,
            required
            | {
                "history_count",
                "seed",
                "analytics",
                "benchmark",
                "max_order_notional",
                "allow_short",
                "short_assumption",
                "allow_zero_costs",
                "boundary_liquidation",
            },
            required,
        )
        seed = _integer(data.get("seed", 0))
        params = {
            "instrument_ids": data["instrument_ids"],
            "budget": data["budget"],
            "history_count": data.get("history_count", 256),
            "backend_identity": backend_identity,
        }
        _, _, budget, _ = self._parameters(params, seed)
        params["budget"] = asdict(budget)
        return self._prepare_config(
            data,
            strategy_id="isolated.python.v1",
            strategy_name="isolated_python",
            parameters=params,
            strategy_source_hash=self.package.source_sha256,
            project_source_hash=hashlib.sha256(
                build_source_bundle(
                    self.project_root, submitted_strategy=self.package.source.encode("utf8")
                )
            ).hexdigest(),
        )

    def strategy(self, config: ExperimentConfig) -> Strategy:
        if config.strategy_id != "isolated.python.v1" or config.strategy_name != "isolated_python":
            raise IntegrityError("Unknown isolated strategy identity")
        if config.strategy_source_hash != self.package.source_sha256:
            raise IntegrityError("Isolated source differs from prepared configuration")
        if config.engine_version != __version__:
            raise IntegrityError("Isolated engine differs from prepared configuration")
        instruments, history, budget, backend = self._parameters(config.parameters, config.seed)
        if backend != self._available():
            raise IntegrityError("Isolated backend differs from prepared configuration")
        if {i.value for i in instruments} != {i for d in config.datasets for i in d.instrument_ids}:
            raise DomainError("Isolated instruments must exactly match pinned datasets")
        return IsolatedStrategy(
            _BoundBackend(self.backend.backend, self.cancel, backend),
            self.package.source,
            instruments,
            budget,
            config.seed,
            history,
        )

    def _source_artifacts(self, config: ExperimentConfig) -> dict[str, bytes]:
        self.strategy(config)
        source = self.package.source.encode("utf8")
        bundle = build_source_bundle(self.project_root, submitted_strategy=source)
        if (
            hashlib.sha256(source).hexdigest() != config.strategy_source_hash
            or hashlib.sha256(bundle).hexdigest() != config.project_source_hash
            or hashlib.sha256(self.lock_path.read_bytes()).hexdigest()
            != config.dependency_lock_hash
        ):
            raise IntegrityError(
                "Isolated source or dependency lock differs from prepared configuration"
            )
        return {"strategy-source.py": source, "source-bundle.zip": bundle}
