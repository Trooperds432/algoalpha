"""Local experiment registry with immutable files and verified SQLite metadata.

This is a trusted-workspace registry, not a strategy sandbox. Only an explicit
``verify_repeat`` runner is executable; stored source artifacts remain bytes.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from uuid import uuid4

from quant_terminal.analytics.metrics import AnalyticsConfig, Metric, RiskOfRuinConfig, analyze
from quant_terminal.backtesting.models import BacktestResult
from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import RunId
from quant_terminal.data.storage import DatasetStore
from quant_terminal.reporting import build_report, export_bundle

from .models import (
    ArtifactDescriptor,
    ExperimentManifest,
    ReplayOutcome,
    canonical_json,
    result_fingerprint,
)


class StoreError(DomainError):
    """A registry operation failed without overwriting a registered run."""


class IntegrityError(StoreError):
    """Registered data disagrees with its canonical identity or checksums."""


class DuplicateRunError(StoreError):
    """The run ID or its unregistered destination already exists."""


class StoreBusyError(StoreError):
    """SQLite could not acquire its writer lock within the bounded timeout."""


class RunNotFoundError(StoreError):
    """The run is not registered; an orphan directory is not a registered run."""


class RepeatMismatchError(StoreError):
    """A repeat was retained under a new ID but produced different economics."""

    def __init__(self, message: str, run_id: RunId):
        super().__init__(message)
        self.run_id = run_id


RepeatRunner = Callable[[ExperimentManifest, Path, DatasetStore], ReplayOutcome]


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _component(value: str) -> str:
    # Reuse the platform-neutral, Windows-safe single-component contract.
    ArtifactDescriptor(value, "0" * 64, 0)
    return value


def _run_text(run_id: RunId | str) -> str:
    text = run_id.value if isinstance(run_id, RunId) else run_id
    if not isinstance(text, str):
        raise StoreError("Run ID must be a RunId or string")
    RunId(text)
    return text


def _directory_name(run_id: RunId | str) -> str:
    return _digest(_run_text(run_id).encode("utf-8"))


def _analytics_config(settings: Mapping[str, object]) -> AnalyticsConfig:
    allowed = {
        "annualization",
        "annual_risk_free_rate",
        "annual_mar",
        "var_confidences",
        "risk_of_ruin",
    }
    if set(settings) - allowed:
        raise StoreError("Unsupported analytics settings in repeat manifest")
    defaults = AnalyticsConfig()

    def decimal(value: object) -> Decimal:
        if not isinstance(value, (str, Decimal)):
            raise StoreError("Analytics decimal inputs must be exact decimal strings")
        try:
            parsed = Decimal(value)
        except ArithmeticError as exc:
            raise StoreError("Invalid analytics Decimal") from exc
        if not parsed.is_finite():
            raise StoreError("Analytics Decimal must be finite")
        return parsed

    def integer(value: object) -> int:
        if type(value) is not int:
            raise StoreError("Analytics integer setting must be an integer")
        return value

    raw_confidences = settings.get("var_confidences", defaults.var_confidences)
    if not isinstance(raw_confidences, (tuple, list)):
        raise StoreError("VaR confidences must be an array")
    raw_ruin = settings.get("risk_of_ruin")
    ruin: RiskOfRuinConfig | None = None
    if raw_ruin is not None:
        if not isinstance(raw_ruin, Mapping) or set(raw_ruin) != {
            "allocation_fraction",
            "ruin_equity_fraction",
            "horizon_trades",
            "paths",
            "seed",
        }:
            raise StoreError("Invalid risk-of-ruin configuration")
        ruin = RiskOfRuinConfig(
            allocation_fraction=decimal(raw_ruin["allocation_fraction"]),
            ruin_equity_fraction=decimal(raw_ruin["ruin_equity_fraction"]),
            horizon_trades=integer(raw_ruin["horizon_trades"]),
            paths=integer(raw_ruin["paths"]),
            seed=integer(raw_ruin["seed"]),
        )
    return AnalyticsConfig(
        annualization=integer(settings.get("annualization", defaults.annualization)),
        annual_risk_free_rate=decimal(
            settings.get("annual_risk_free_rate", defaults.annual_risk_free_rate)
        ),
        annual_mar=decimal(settings.get("annual_mar", defaults.annual_mar)),
        var_confidences=tuple(decimal(item) for item in raw_confidences),
        risk_of_ruin=ruin,
    )


def _safe_path(path: Path, boundary: Path | None = None) -> Path:
    """Reject link/junction components before resolving a trusted local path."""
    path = path.absolute()
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise IntegrityError("Symlink or junction paths are not permitted")
    resolved = path.resolve()
    if boundary is not None and not resolved.is_relative_to(boundary):
        raise IntegrityError("Path escapes the experiment store")
    return resolved


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    # Windows has no portable directory fsync; each file is flushed before rename.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class ExperimentStore:
    """SQLite index and verified immutable artifacts for completed research runs."""

    def __init__(self, root: Path, *, busy_timeout_ms: int = 5000):
        if type(busy_timeout_ms) is not int or not 0 <= busy_timeout_ms <= 60000:
            raise StoreError("busy_timeout_ms must be an integer from 0 through 60000")
        self.root = _safe_path(Path(root))
        self.busy_timeout_ms = busy_timeout_ms
        self.runs_dir = self.root / "runs"
        self.staging_dir = self.root / "staging"
        self.db_path = self.root / "experiments.sqlite3"
        try:
            for path in (self.root, self.runs_dir, self.staging_dir):
                _safe_path(path, self.root)
                path.mkdir(parents=True, exist_ok=True)
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                schema = connection.execute("PRAGMA user_version").fetchone()[0]
                if schema not in (0, 1):
                    raise IntegrityError("Unsupported experiment registry schema version")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS runs ("
                    "run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                    "strategy_id TEXT NOT NULL, input_fingerprint TEXT NOT NULL, "
                    "result_fingerprint TEXT NOT NULL, manifest BLOB NOT NULL, "
                    "manifest_sha256 TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS artifacts ("
                    "run_id TEXT NOT NULL REFERENCES runs(run_id), "
                    "name TEXT NOT NULL COLLATE NOCASE, sha256 TEXT NOT NULL, "
                    "size_bytes INTEGER NOT NULL, PRIMARY KEY(run_id, name))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS tag_events ("
                    "event_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "run_id TEXT NOT NULL REFERENCES runs(run_id), tag TEXT NOT NULL, "
                    "action TEXT NOT NULL CHECK(action IN ('add','remove')), "
                    "created_at TEXT NOT NULL)"
                )
                for name, columns in (
                    ("runs_created", "created_at,run_id"),
                    ("runs_strategy", "strategy_id,created_at,run_id"),
                    ("runs_input", "input_fingerprint,created_at,run_id"),
                ):
                    connection.execute(f"CREATE INDEX IF NOT EXISTS {name} ON runs({columns})")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS tag_lookup ON tag_events(run_id,tag,event_id)"
                )
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS tags_no_{operation.lower()} "
                        f"BEFORE {operation} ON tag_events BEGIN "
                        "SELECT RAISE(ABORT, 'tag events are append-only'); END"
                    )
                    for table in ("runs", "artifacts"):
                        connection.execute(
                            f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} "
                            f"BEFORE {operation} ON {table} BEGIN "
                            "SELECT RAISE(ABORT, 'experiment records are immutable'); END"
                        )
                connection.execute("PRAGMA user_version=1")
                connection.commit()
        except OSError as exc:
            raise StoreError("Unable to initialize experiment storage") from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        _safe_path(self.db_path, self.root)
        for suffix in ("-journal", "-wal", "-shm"):
            _safe_path(Path(str(self.db_path) + suffix), self.root)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.db_path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            yield connection
        except sqlite3.Error as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise StoreBusyError("Experiment store is busy") from exc
            raise IntegrityError("Experiment SQLite operation failed") from exc
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    def _run_directory(self, run_id: RunId | str) -> Path:
        return _safe_path(self.runs_dir / _directory_name(run_id), self.root)

    @staticmethod
    def _source_hashes(manifest: ExperimentManifest, artifacts: Mapping[str, bytes]) -> None:
        required = {
            "source-bundle.zip": manifest.config.project_source_hash,
            "strategy-source.py": manifest.config.strategy_source_hash,
        }
        for name, expected in required.items():
            if name not in artifacts or _digest(artifacts[name]) != expected:
                raise IntegrityError(f"Required source artifact hash mismatch: {name}")
        try:
            with zipfile.ZipFile(io.BytesIO(artifacts["source-bundle.zip"])) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if (
                    not entries
                    or names != sorted(names)
                    or len(set(name.casefold() for name in names)) != len(names)
                    or archive.comment
                ):
                    raise IntegrityError("Source archive must contain unique sorted source files")
                found_strategy = False
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    if (
                        path.is_absolute()
                        or not path.parts
                        or ".." in path.parts
                        or "\\" in entry.filename
                        or ":" in entry.filename
                        or path.as_posix() != entry.filename
                        or entry.is_dir()
                        or entry.date_time != (1980, 1, 1, 0, 0, 0)
                        or entry.compress_type != zipfile.ZIP_STORED
                        or entry.create_system != 3
                        or entry.external_attr != (stat.S_IFREG | 0o644) << 16
                        or entry.internal_attr
                        or entry.extra
                        or entry.comment
                    ):
                        raise IntegrityError(
                            "Source archive entries violate the canonical contract"
                        )
                    content = archive.read(entry)
                    found_strategy |= _digest(content) == manifest.config.strategy_source_hash
                if not found_strategy:
                    raise IntegrityError(
                        "Strategy source bytes are absent from the project archive"
                    )
        except IntegrityError:
            raise
        except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
            raise IntegrityError("Source bundle is not a valid canonical ZIP archive") from exc

    def create(
        self,
        manifest: ExperimentManifest,
        outcome: ReplayOutcome,
        artifact_bytes: Mapping[str, bytes],
    ) -> ExperimentManifest:
        """Finalize durable bytes before atomically registering their canonical manifest."""
        if not isinstance(manifest, ExperimentManifest):
            raise StoreError("A typed ExperimentManifest is required")
        run_id = _run_text(manifest.run_id)
        result = self._verify_outcome_inputs(manifest, outcome)
        if result_fingerprint(result) != manifest.result_fingerprint:
            raise IntegrityError("Result fingerprint does not match the manifest")
        if result.start != manifest.config.start or result.end != manifest.config.end:
            raise IntegrityError("Result interval does not match experiment inputs")
        if not isinstance(artifact_bytes, Mapping):
            raise StoreError("Artifact inputs must be a mapping of names to bytes")
        artifacts: dict[str, bytes] = {}
        names: set[str] = set()
        for name, data in artifact_bytes.items():
            _component(name)
            if name.casefold() == "manifest.json" or name.casefold() in names:
                raise StoreError("Artifact names conflict with each other or manifest.json")
            if type(data) is not bytes:
                raise StoreError("Artifact source inputs must be bytes, never filesystem paths")
            names.add(name.casefold())
            artifacts[name] = data
        self._source_hashes(manifest, artifacts)
        descriptors = tuple(
            ArtifactDescriptor(name, _digest(data), len(data))
            for name, data in sorted(artifacts.items())
        )
        if manifest.artifacts and sorted(manifest.artifacts, key=lambda item: item.name) != list(
            descriptors
        ):
            raise IntegrityError("Supplied artifact descriptors do not match artifact bytes")
        final = replace(manifest, artifacts=descriptors)
        manifest_bytes = canonical_json(final.to_dict())
        target = self._run_directory(run_id)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
                    raise DuplicateRunError("Run ID is already registered")
                if target.exists():
                    raise DuplicateRunError("Unregistered run directory already exists")
                stage = _safe_path(self.staging_dir / ("stage-" + uuid4().hex), self.root)
                stage.mkdir()
                for name, data in sorted(artifacts.items()):
                    _write_bytes(stage / name, data)
                _write_bytes(stage / "manifest.json", manifest_bytes)
                self._verify_files(final, stage, manifest_bytes)
                _sync_directory(stage)
                # A failed rename or transaction leaves explicit recovery evidence.
                _safe_path(target, self.root)
                if target.exists():
                    raise DuplicateRunError("Run destination appeared during creation")
                stage.rename(target)
                _sync_directory(self.runs_dir)
                connection.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                    (
                        run_id,
                        final.created_at.isoformat(),
                        final.config.strategy_id,
                        final.input_fingerprint,
                        final.result_fingerprint,
                        manifest_bytes,
                        _digest(manifest_bytes),
                    ),
                )
                connection.executemany(
                    "INSERT INTO artifacts(run_id,name,sha256,size_bytes) VALUES (?,?,?,?)",
                    [(run_id, item.name, item.sha256, item.size_bytes) for item in descriptors],
                )
                connection.executemany(
                    "INSERT INTO tag_events(run_id,tag,action,created_at) VALUES (?,?,?,?)",
                    [(run_id, tag, "add", final.created_at.isoformat()) for tag in final.tags],
                )
                connection.commit()
        except OSError as exc:
            raise StoreError(
                "Unable to finalize experiment artifacts; recovery may be needed"
            ) from exc
        return final

    @staticmethod
    def _verify_outcome_inputs(
        manifest: ExperimentManifest, outcome: ReplayOutcome
    ) -> BacktestResult:
        if not isinstance(outcome, ReplayOutcome):
            raise StoreError("Registration requires an identity-attested ReplayOutcome")
        config = manifest.config
        if (
            outcome.strategy_source_hash != config.strategy_source_hash
            or outcome.project_source_hash != config.project_source_hash
            or outcome.datasets != config.datasets
            or outcome.input_fingerprint != manifest.input_fingerprint
        ):
            raise IntegrityError("Outcome identities do not match the exact experiment inputs")
        result = outcome.result
        if result.start != config.start or result.end != config.end:
            raise IntegrityError("Result interval does not match experiment inputs")
        if (
            not result.nav
            or result.nav[0].timestamp != config.start
            or result.nav[0].nav != config.initial_capital
        ):
            raise IntegrityError("Initial NAV checkpoint does not match experiment capital/start")
        try:
            metadata = dict(result.metadata)
        except (TypeError, ValueError) as exc:
            raise IntegrityError("Invalid M5 result metadata") from exc
        if len(metadata) != len(result.metadata) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise IntegrityError("M5 metadata keys must be unique and values must be strings")
        backtest = config.backtest_settings
        execution = config.execution_assumptions
        required_backtest = {
            "allow_short",
            "allow_zero_costs",
            "max_order_notional",
            "short_assumption",
        }
        required_execution = {
            "maximum_participation",
            "commission_model_repr",
            "slippage_model_repr",
            "zero_cost_assumption",
        }
        if not required_backtest.issubset(backtest) or not required_execution.issubset(execution):
            raise IntegrityError("Experiment configuration is missing explicit M5 input settings")
        for field in ("allow_short", "allow_zero_costs"):
            if type(backtest[field]) is not bool:
                raise IntegrityError(f"M5 configuration {field} must be boolean")
        short_assumption = backtest["short_assumption"]
        if short_assumption is not None and (
            not isinstance(short_assumption, str) or not short_assumption.strip()
        ):
            raise IntegrityError("Invalid configured short assumption")
        zero_cost = execution["zero_cost_assumption"]
        if zero_cost not in (None, "ZERO_COST_ASSUMPTION"):
            raise IntegrityError("Invalid configured zero-cost assumption")
        if zero_cost is not None and not backtest["allow_zero_costs"]:
            raise IntegrityError("Zero-cost execution was not permitted by the configuration")
        benchmark = ""
        if config.benchmark is not None:
            configured = config.benchmark.get("instrument_id")
            if not isinstance(configured, str) or not configured:
                raise IntegrityError("Benchmark requires an explicit instrument_id")
            benchmark = configured
        expected: dict[str, object] = {
            "strategy": config.strategy_name,
            "dataset_snapshots": ",".join(item.snapshot_id for item in config.datasets),
            "currency": config.currency,
            "start": config.start.isoformat(),
            "end": config.end.isoformat(),
            "allow_short": str(backtest["allow_short"]),
            "allow_zero_costs": str(backtest["allow_zero_costs"]),
            "short_assumption": short_assumption or "",
            "commission_model": execution["commission_model_repr"],
            "slippage_model": execution["slippage_model_repr"],
            "cost_assumption": zero_cost or "configured_costs",
            "benchmark": benchmark,
        }
        if result.strategy_name != config.strategy_name:
            raise IntegrityError("Result strategy_name does not match experiment configuration")
        if "robustness_overlay" in backtest:
            expected["robustness_overlay"] = canonical_json(backtest["robustness_overlay"]).decode(
                "utf-8"
            )
        elif "robustness_overlay" in metadata:
            raise IntegrityError("Unpinned robustness overlay in result metadata")
        boundary = backtest.get("boundary_liquidation", False)
        if type(boundary) is not bool:
            raise IntegrityError("Boundary liquidation setting must be boolean")
        if boundary:
            expected["boundary_liquidation"] = "scheduled_final_close_v1"
        elif "boundary_liquidation" in metadata:
            raise IntegrityError("Unpinned boundary liquidation in result metadata")
        for field, value in expected.items():
            if not isinstance(value, str) or metadata.get(field) != value:
                raise IntegrityError(f"M5 result metadata {field} does not match configuration")
        for field, value in (
            ("initial_cash", config.initial_capital),
            ("max_order_notional", backtest["max_order_notional"]),
            ("max_participation", execution["maximum_participation"]),
        ):
            if not isinstance(value, (str, Decimal)):
                raise IntegrityError(f"Configured {field} must be an exact Decimal")
            try:
                actual = Decimal(metadata[field])
                configured_decimal = Decimal(value)
            except (KeyError, ArithmeticError, ValueError) as exc:
                raise IntegrityError(f"Missing or invalid M5 metadata {field}") from exc
            if (
                not actual.is_finite()
                or not configured_decimal.is_finite()
                or actual != configured_decimal
            ):
                raise IntegrityError(f"M5 result metadata {field} does not match configuration")
        if any(point.instrument_id.value != benchmark for point in result.benchmark):
            raise IntegrityError("Benchmark observations do not match experiment configuration")
        return result

    @staticmethod
    def _report_bytes(result: BacktestResult, config: AnalyticsConfig) -> bytes:
        """Regenerate report content from this result, never reuse an earlier run's report."""
        report = build_report(result, config)
        output = io.BytesIO()
        with tempfile.TemporaryDirectory(prefix="quant-repeat-report-") as temporary:
            root = _safe_path(Path(temporary))
            directory = export_bundle(report, root / "report")
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                for path in sorted(directory.rglob("*")):
                    _safe_path(path, root)
                    if path.is_file():
                        entry = zipfile.ZipInfo(
                            path.relative_to(directory).as_posix(), date_time=(1980, 1, 1, 0, 0, 0)
                        )
                        entry.create_system = 3
                        entry.external_attr = (stat.S_IFREG | 0o644) << 16
                        archive.writestr(entry, path.read_bytes())
        return output.getvalue()

    def _verify_files(
        self, manifest: ExperimentManifest, directory: Path, manifest_bytes: bytes
    ) -> dict[str, bytes]:
        _safe_path(directory, self.root)
        try:
            expected = {"manifest.json", *(item.name for item in manifest.artifacts)}
            if not directory.is_dir() or {path.name for path in directory.iterdir()} != expected:
                raise IntegrityError("Run directory contents do not match the manifest")
            stored = _safe_path(directory / "manifest.json", self.root)
            if not stored.is_file() or stored.read_bytes() != manifest_bytes:
                raise IntegrityError("Stored manifest bytes do not match the SQLite identity")
            artifacts: dict[str, bytes] = {}
            for item in manifest.artifacts:
                path = _safe_path(directory / item.name, self.root)
                if not path.is_file():
                    raise IntegrityError("Artifact is not a regular file")
                data = path.read_bytes()
                if len(data) != item.size_bytes or _digest(data) != item.sha256:
                    raise IntegrityError(f"Artifact size/hash mismatch: {item.name}")
                artifacts[item.name] = data
            self._source_hashes(manifest, artifacts)
            return artifacts
        except OSError as exc:
            raise IntegrityError("Unable to read registered experiment artifacts") from exc

    def get(self, run_id: RunId | str) -> ExperimentManifest:
        """Verify both copies of the canonical manifest and every declared artifact."""
        identity = _run_text(run_id)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (identity,)).fetchone()
            if row is None:
                raise RunNotFoundError("Run ID is not registered")
            records = connection.execute(
                "SELECT name,sha256,size_bytes FROM artifacts WHERE run_id=? ORDER BY name",
                (identity,),
            ).fetchall()
        data = row["manifest"]
        if type(data) is not bytes or _digest(data) != row["manifest_sha256"]:
            raise IntegrityError("SQLite manifest digest mismatch")
        try:
            manifest = ExperimentManifest.from_dict(json.loads(data))
            if canonical_json(manifest.to_dict()) != data:
                raise IntegrityError("SQLite manifest is not canonical")
        except (DomainError, ValueError, TypeError, KeyError) as exc:
            raise IntegrityError("Invalid SQLite experiment manifest") from exc
        if (
            manifest.run_id.value != identity
            or manifest.created_at.isoformat() != row["created_at"]
            or manifest.config.strategy_id != row["strategy_id"]
            or manifest.input_fingerprint != row["input_fingerprint"]
            or manifest.result_fingerprint != row["result_fingerprint"]
        ):
            raise IntegrityError("SQLite indexed metadata disagrees with the manifest")
        expected = sorted((item.name, item.sha256, item.size_bytes) for item in manifest.artifacts)
        actual = sorted((item["name"], item["sha256"], item["size_bytes"]) for item in records)
        if actual != expected:
            raise IntegrityError("SQLite artifact descriptors disagree with the manifest")
        self._verify_files(manifest, self._run_directory(identity), data)
        return manifest

    def read_artifact(self, run_id: RunId | str, name: str) -> bytes:
        _component(name)
        manifest = self.get(run_id)
        if name not in {item.name for item in manifest.artifacts}:
            raise StoreError("Artifact is not declared by this run")
        # Return the same bytes that were checked, not a second unchecked read.
        return self._verify_files(
            manifest, self._run_directory(run_id), canonical_json(manifest.to_dict())
        )[name]

    def search(
        self,
        *,
        strategy_id: str | None = None,
        input_fingerprint: str | None = None,
        tag: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[ExperimentManifest, ...]:
        """Search verified runs by creation time in the half-open interval [start,end)."""
        lower = utc(start) if start is not None else None
        upper = utc(end) if end is not None else None
        if lower is not None and upper is not None and lower > upper:
            raise StoreError("Search start is after end")
        clauses: list[str] = []
        values: list[str] = []
        for column, value in (
            ("strategy_id", strategy_id),
            ("input_fingerprint", input_fingerprint),
        ):
            if value is not None:
                if not isinstance(value, str):
                    raise StoreError("Search filters must be strings")
                clauses.append(f"r.{column}=?")
                values.append(value)
        for operator, date_bound in ((">=", lower), ("<", upper)):
            if date_bound is not None:
                clauses.append(f"r.created_at {operator} ?")
                values.append(date_bound.isoformat())
        if tag is not None:
            self._tag_text(tag)
            clauses.append(
                "EXISTS (SELECT 1 FROM tag_events t WHERE t.run_id=r.run_id AND t.tag=? "
                "AND t.action='add' AND t.event_id=(SELECT MAX(u.event_id) FROM tag_events u "
                "WHERE u.run_id=t.run_id AND u.tag=t.tag))"
            )
            values.append(tag)
        query = "SELECT r.run_id FROM runs r"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY r.created_at,r.run_id"
        with self._connection() as connection:
            identities = [row[0] for row in connection.execute(query, values).fetchall()]
        return tuple(self.get(identity) for identity in identities)

    @staticmethod
    def _tag_text(tag: str) -> str:
        if not isinstance(tag, str) or not tag.strip() or len(tag) > 256:
            raise StoreError("Tag must be a nonempty string of at most 256 characters")
        return tag

    def _tag_event(self, run_id: RunId | str, tag: str, action: str) -> None:
        identity = _run_text(run_id)
        self._tag_text(tag)
        self.get(identity)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO tag_events(run_id,tag,action,created_at) VALUES (?,?,?,?)",
                (identity, tag, action, datetime.now(UTC).isoformat()),
            )
            connection.commit()

    def tag(self, run_id: RunId | str, tag: str) -> None:
        self._tag_event(run_id, tag, "add")

    def untag(self, run_id: RunId | str, tag: str) -> None:
        self._tag_event(run_id, tag, "remove")

    def tags(self, run_id: RunId | str) -> tuple[str, ...]:
        """Return current tags; the immutable manifest retains its original tags."""
        identity = self.get(run_id).run_id.value
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT t.tag FROM tag_events t WHERE t.run_id=? AND t.action='add' "
                "AND t.event_id=(SELECT MAX(u.event_id) FROM tag_events u "
                "WHERE u.run_id=t.run_id AND u.tag=t.tag) ORDER BY t.tag",
                (identity,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def compare(self, run_ids: Sequence[RunId | str]) -> dict[str, dict[str, Metric | None]]:
        if isinstance(run_ids, (str, bytes)):
            raise StoreError("Comparison requires a sequence of run IDs")
        manifests = [self.get(run_id) for run_id in run_ids]
        if len({manifest.run_id.value for manifest in manifests}) != len(manifests):
            raise StoreError("Comparison run IDs must be unique")
        names = sorted({name for manifest in manifests for name, _ in manifest.metrics})
        return {
            name: {
                manifest.run_id.value: dict(manifest.metrics).get(name) for manifest in manifests
            }
            for name in names
        }

    def clone_config(self, run_id: RunId | str) -> dict[str, object]:
        # to_dict builds new nested JSON containers, detached from the frozen model.
        return self.get(run_id).config.to_dict()

    @staticmethod
    def _verify_datasets(manifest: ExperimentManifest, store: DatasetStore) -> None:
        if not isinstance(store, DatasetStore):
            raise StoreError("Repeat verification requires an explicit DatasetStore")
        for expected in manifest.config.datasets:
            snapshot = store.load_snapshot(expected.snapshot_id)
            if (
                snapshot.snapshot_id != expected.snapshot_id
                or snapshot.content_hash != expected.content_hash
                or snapshot.input_hash != expected.input_hash
                or snapshot.row_count != expected.row_count
                or (snapshot.instrument.instrument_id.value,) != expected.instrument_ids
            ):
                raise IntegrityError("Dataset identity does not match repeat inputs")

    def verify_repeat(
        self,
        original_run_id: RunId | str,
        runner: RepeatRunner,
        dataset_store: DatasetStore,
    ) -> ExperimentManifest:
        original = self.get(original_run_id)
        if not callable(runner):
            raise StoreError("Repeat requires an explicit callable runner")
        self._verify_datasets(original, dataset_store)
        artifacts = self._verify_files(
            original,
            self._run_directory(original.run_id),
            canonical_json(original.to_dict()),
        )
        outcome = runner(original, self._run_directory(original.run_id), dataset_store)
        repeated = self._verify_outcome_inputs(original, outcome)
        # Reject mutation of the original artifacts or data while the trusted adapter ran.
        checked = self.get(original.run_id)
        if checked.input_fingerprint != original.input_fingerprint:
            raise IntegrityError("Repeat input fingerprint changed")
        self._verify_datasets(original, dataset_store)
        fingerprint = result_fingerprint(repeated)
        matches = fingerprint == original.result_fingerprint
        analytics_config = _analytics_config(original.config.analytics_settings)
        metrics = analyze(repeated, analytics_config).metrics
        retained_artifacts = {
            name: data
            for name, data in artifacts.items()
            if name in {"source-bundle.zip", "strategy-source.py"}
        }
        omitted_artifacts = tuple(
            sorted(set(artifacts) - set(retained_artifacts) - {"report-bundle.zip"})
        )
        retained_artifacts["report-bundle.zip"] = self._report_bytes(repeated, analytics_config)
        metadata = dict(original.runtime_metadata)
        metadata.update(
            repeat_of=original.run_id.value,
            repeat_status="matched" if matches else "mismatched",
            retained_artifacts_from=original.run_id.value,
            regenerated_artifacts=("report-bundle.zip",),
            omitted_artifacts=omitted_artifacts,
        )
        new = replace(
            original,
            run_id=RunId.new(),
            created_at=datetime.now(UTC),
            result_fingerprint=fingerprint,
            metrics=metrics,
            runtime_metadata=metadata,
            artifacts=(),
        )
        final = self.create(new, outcome, retained_artifacts)
        if not matches:
            raise RepeatMismatchError(
                "Repeated run produced different economic output", final.run_id
            )
        return final

    def recovery_candidates(self) -> tuple[Path, ...]:
        """List only unregistered directories; never classify corrupt registered runs as orphans."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            registered = {
                _directory_name(row[0]) for row in connection.execute("SELECT run_id FROM runs")
            }
            candidates: list[Path] = []
            for parent in (self.staging_dir, self.runs_dir):
                _safe_path(parent, self.root)
                for path in parent.iterdir():
                    if parent == self.runs_dir and path.name in registered:
                        continue
                    _safe_path(path, self.root)
                    if path.is_dir():
                        self._check_recovery_tree(path)
                        candidates.append(path)
            connection.commit()
        return tuple(sorted(candidates, key=lambda path: str(path)))

    def _check_recovery_tree(self, path: Path) -> None:
        _safe_path(path, self.root)
        for parent, directories, files in os.walk(path, followlinks=False):
            for name in (*directories, *files):
                _safe_path(Path(parent) / name, self.root)

    def remove_unregistered(self, path: Path) -> None:
        """Remove one explicitly selected staging/orphan directory, under the writer lock."""
        candidate = _safe_path(Path(path), self.root)
        if candidate.parent not in (self.staging_dir, self.runs_dir) or not candidate.is_dir():
            raise StoreError("Recovery requires a direct staging or orphan directory")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            registered = {
                _directory_name(row[0]) for row in connection.execute("SELECT run_id FROM runs")
            }
            if candidate.parent == self.runs_dir and candidate.name in registered:
                raise StoreError("Registered run directories cannot be removed by recovery")
            self._check_recovery_tree(candidate)
            try:
                shutil.rmtree(candidate)
            except OSError as exc:
                raise StoreError("Unable to remove selected unregistered directory") from exc
            connection.commit()
