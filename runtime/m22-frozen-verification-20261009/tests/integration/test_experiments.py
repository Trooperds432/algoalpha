"""Experiment registry checks using real M2, M5, M6, and M7 outputs."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quant_terminal.analytics import AnalyticsConfig, Metric, analyze
from quant_terminal.backtesting.engine import BacktestEngine
from quant_terminal.backtesting.models import BacktestConfig, BacktestResult
from quant_terminal.backtesting.strategies import BuyAndHold
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId, RunId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.data.storage import DatasetSnapshot, DatasetStore
from quant_terminal.execution.models import (
    ConfiguredSlippage,
    ExecutionConfig,
    PerUnitCommission,
    SlippageKind,
)
from quant_terminal.experiments import (
    DatasetIdentity,
    ExperimentConfig,
    ExperimentManifest,
    ExperimentStore,
    IntegrityError,
    RepeatMismatchError,
    ReplayOutcome,
    StoreBusyError,
    build_source_bundle,
    result_fingerprint,
)
from quant_terminal.reporting import build_report, export_bundle

PROJECT = Path(__file__).parents[2]
SOURCE_DATA = PROJECT / "examples" / "data" / "synthetic_xnys_2026.csv"
STRATEGY_SOURCE = PROJECT / "src" / "quant_terminal" / "backtesting" / "strategies.py"
START = datetime(2026, 11, 25, 14, 30, tzinfo=UTC)
END = datetime(2026, 11, 28, 0, 0, tzinfo=UTC)
INSTRUMENT = InstrumentMeta(
    InstrumentId("ETF-1"),
    "TEST",
    "USD",
    "etf",
    "XNYS",
    "America/New_York",
    Decimal("0.01"),
    Decimal("1"),
)


@pytest.fixture(scope="session")
def source_artifacts() -> dict[str, bytes]:
    source_bundle = build_source_bundle(PROJECT)
    return {
        "source-bundle.zip": source_bundle,
        "strategy-source.py": STRATEGY_SOURCE.read_bytes(),
    }


def _input_dataset(directory: Path) -> tuple[DatasetStore, DatasetSnapshot]:
    store = DatasetStore(directory)
    snapshot = store.import_csv(
        SOURCE_DATA,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    return store, snapshot


def _run(
    directory: Path, *, quantity: Decimal = Decimal("10")
) -> tuple[DatasetStore, DatasetSnapshot, BacktestResult]:
    dataset_store, snapshot = _input_dataset(directory)
    engine = BacktestEngine(
        dataset_store,
        (snapshot,),
        BuyAndHold(INSTRUMENT.instrument_id, quantity),
        BacktestConfig(Decimal("10000"), "USD", max_order_notional=Decimal("1000000")),
        ExecutionConfig(commission=PerUnitCommission(Decimal("0.1"))),
    )
    return (
        dataset_store,
        snapshot,
        engine.run(start=START, end=END, benchmark=INSTRUMENT.instrument_id),
    )


def _manifest(
    snapshot: DatasetSnapshot,
    result: BacktestResult,
    source_artifacts: dict[str, bytes],
    *,
    run_id: str = "run-original",
    created_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    parameters: dict[str, object] | None = None,
) -> ExperimentManifest:
    source_hash = hashlib.sha256(source_artifacts["strategy-source.py"]).hexdigest()
    project_hash = hashlib.sha256(source_artifacts["source-bundle.zip"]).hexdigest()
    config = ExperimentConfig(
        strategy_id="builtin.buy-and-hold.v1",
        strategy_name=result.strategy_name,
        strategy_source_hash=source_hash,
        project_source_hash=project_hash,
        git_commit=None,
        engine_version="0.0.1",
        dependency_lock_hash=hashlib.sha256(
            (PROJECT / "requirements-dev.lock").read_bytes()
        ).hexdigest(),
        datasets=(
            DatasetIdentity(
                snapshot.snapshot_id,
                snapshot.content_hash,
                snapshot.input_hash,
                snapshot.row_count,
                (snapshot.instrument.instrument_id.value,),
            ),
        ),
        start=START,
        end=END,
        currency="USD",
        initial_capital=Decimal("10000"),
        parameters=parameters if parameters is not None else {"quantity": Decimal("10")},
        seed=20260927,
        backtest_settings={
            "allow_short": False,
            "allow_zero_costs": False,
            "max_order_notional": Decimal("1000000"),
            "short_assumption": None,
        },
        analytics_settings={
            "annualization": 252,
            "annual_risk_free_rate": Decimal(0),
            "annual_mar": Decimal(0),
            "var_confidences": (Decimal("0.95"), Decimal("0.99"), Decimal("0.999")),
            "risk_of_ruin": None,
        },
        benchmark={"instrument_id": INSTRUMENT.instrument_id.value},
        execution_assumptions={
            "maximum_participation": Decimal(1),
            "commission": {"kind": "per_unit", "per_unit_rate": Decimal("0.1"), "minimum": None},
            "commission_model_repr": repr(PerUnitCommission(Decimal("0.1"))),
            "slippage": {"kind": "none", "value": Decimal(0)},
            "slippage_model_repr": repr(ConfiguredSlippage()),
            "zero_cost_assumption": None,
        },
    )
    return ExperimentManifest(
        RunId(run_id),
        created_at,
        config,
        result_fingerprint(result),
        analyze(result, AnalyticsConfig()).metrics,
        "Python test runtime",
        {"host": "fixture", "os": "Windows"},
    )


def _record(
    root: Path,
    data_root: Path,
    source_artifacts: dict[str, bytes],
    *,
    run_id: str = "run-original",
) -> tuple[ExperimentStore, DatasetStore, DatasetSnapshot, BacktestResult, ExperimentManifest]:
    data_store, snapshot, result = _run(data_root)
    report_dir = export_bundle(build_report(result), root.parent / f"report-{run_id}")
    report_bytes = io.BytesIO()
    with zipfile.ZipFile(report_bytes, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path in sorted(report_dir.rglob("*")):
            if path.is_file():
                bundle.writestr(path.relative_to(report_dir).as_posix(), path.read_bytes())
    shutil.rmtree(report_dir)
    artifacts = {
        **source_artifacts,
        "report-bundle.zip": report_bytes.getvalue(),
        "result-summary.txt": b"captured with the original result",
    }
    manifest = _manifest(snapshot, result, source_artifacts, run_id=run_id)
    registry = ExperimentStore(root)
    stored = registry.create(manifest, _outcome(manifest, result, source_artifacts), artifacts)
    return registry, data_store, snapshot, result, stored


def _outcome(
    manifest: ExperimentManifest,
    result: BacktestResult,
    source_artifacts: dict[str, bytes],
) -> ReplayOutcome:
    return ReplayOutcome(
        result,
        hashlib.sha256(source_artifacts["strategy-source.py"]).hexdigest(),
        hashlib.sha256(source_artifacts["source-bundle.zip"]).hexdigest(),
        manifest.config.datasets,
        manifest.input_fingerprint,
    )


def test_registry_roundtrip_search_tags_compare_clone_and_verify_report_artifact(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, _, _, _, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )
    loaded = store.get(manifest.run_id)
    report_bundle = store.read_artifact(manifest.run_id, "report-bundle.zip")

    assert loaded == manifest
    assert zipfile.ZipFile(io.BytesIO(report_bundle)).namelist()
    assert store.search(strategy_id=manifest.config.strategy_id) == (manifest,)
    assert store.search(input_fingerprint=manifest.input_fingerprint) == (manifest,)
    assert store.search(start=manifest.created_at, end=manifest.created_at + timedelta(seconds=1))
    store.tag(manifest.run_id, "approved-for-research")
    assert store.tags(manifest.run_id) == ("approved-for-research",)
    assert store.search(tag="approved-for-research") == (manifest,)
    store.untag(manifest.run_id, "approved-for-research")
    assert store.tags(manifest.run_id) == ()
    assert store.search(tag="approved-for-research") == ()
    assert (
        store.compare((manifest.run_id,))["sharpe"][manifest.run_id.value]
        == dict(manifest.metrics)["sharpe"]
    )
    clone = store.clone_config(manifest.run_id)
    clone["parameters"]["quantity"] = "999"  # type: ignore[index]
    assert store.get(manifest.run_id).config.parameters["quantity"] == "10"
    connection = sqlite3.connect(store.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE tag_events SET tag='rewritten'")
    finally:
        connection.close()


def test_metric_comparison_preserves_unavailable_reason(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, _, _, result, first = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )
    metrics = dict(first.metrics)
    metrics["sharpe"] = Metric(None, "ratio", 2, "unavailable", "zero return variance")
    second = replace(
        first,
        run_id=RunId("run-second"),
        created_at=first.created_at + timedelta(seconds=1),
        metrics=tuple(sorted(metrics.items())),
    )
    artifacts = {
        **source_artifacts,
        "report-bundle.zip": store.read_artifact(first.run_id, "report-bundle.zip"),
        "result-summary.txt": store.read_artifact(first.run_id, "result-summary.txt"),
    }
    store.create(second, _outcome(second, result, source_artifacts), artifacts)

    comparison = store.compare((first.run_id, second.run_id))

    assert comparison["sharpe"][first.run_id.value] is not None
    assert comparison["sharpe"][second.run_id.value] == Metric(
        None, "ratio", 2, "unavailable", "zero return variance"
    )


def test_sqlite_manifest_digest_is_verified_on_read(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, _, _, _, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )
    connection = sqlite3.connect(store.db_path)
    try:
        connection.execute("DROP TRIGGER runs_no_update")
        connection.execute(
            "UPDATE runs SET manifest=? WHERE run_id=?",
            (b"tampered manifest", manifest.run_id.value),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IntegrityError, match="manifest digest"):
        store.get(manifest.run_id)


def test_create_rejects_duplicate_id_without_replacing_registered_run(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, _, _, result, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )
    before = store.read_artifact(manifest.run_id, "strategy-source.py")
    duplicate_artifacts = {
        **source_artifacts,
        "report-bundle.zip": store.read_artifact(manifest.run_id, "report-bundle.zip"),
        "result-summary.txt": store.read_artifact(manifest.run_id, "result-summary.txt"),
    }

    with pytest.raises(DomainError, match="already registered"):
        store.create(manifest, _outcome(manifest, result, source_artifacts), duplicate_artifacts)

    assert store.read_artifact(manifest.run_id, "strategy-source.py") == before


def test_registered_artifact_tamper_is_detected_and_not_recoverable(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, _, _, _, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )
    run_directory = store.runs_dir / hashlib.sha256(manifest.run_id.value.encode()).hexdigest()
    (run_directory / "strategy-source.py").write_bytes(b"tampered")

    with pytest.raises(IntegrityError, match="size/hash"):
        store.get(manifest.run_id)
    assert run_directory not in store.recovery_candidates()


def test_recovery_only_removes_explicit_unregistered_staging_directory(
    tmp_path: Path,
) -> None:
    store = ExperimentStore(tmp_path / "experiments")
    orphan = store.staging_dir / "stage-orphan"
    orphan.mkdir()
    (orphan / "partial").write_bytes(b"partial")

    assert store.recovery_candidates() == (orphan,)
    with pytest.raises(DomainError, match="direct staging or orphan"):
        store.remove_unregistered(store.root)
    store.remove_unregistered(orphan)
    assert not orphan.exists()


def test_same_id_concurrent_creation_serializes_without_overwrite(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    data_store, snapshot, result = _run(tmp_path / "dataset")
    manifest = _manifest(snapshot, result, source_artifacts)
    first = ExperimentStore(tmp_path / "experiments")
    second = ExperimentStore(tmp_path / "experiments")
    artifacts = {**source_artifacts, "empty.txt": b"same"}
    outcome = _outcome(manifest, result, source_artifacts)

    def create(store: ExperimentStore):
        try:
            return store.create(manifest, outcome, artifacts)
        except Exception as exc:  # exactly one writer must lose the same-ID race
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(create, (first, second)))

    assert sum(isinstance(outcome, ExperimentManifest) for outcome in outcomes) == 1
    assert sum(type(outcome).__name__ == "DuplicateRunError" for outcome in outcomes) == 1
    assert len(first.search()) == 1
    del data_store


def test_bounded_sqlite_busy_error_does_not_append_a_tag(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, _, _, _, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )
    busy_store = ExperimentStore(store.root, busy_timeout_ms=0)
    connection = sqlite3.connect(store.db_path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(StoreBusyError):
            busy_store.tag(manifest.run_id, "blocked")
        connection.rollback()
    finally:
        connection.close()
    assert store.tags(manifest.run_id) == ()


def test_symlinked_store_paths_are_rejected_without_touching_outside_files(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"keep")
    linked_root = tmp_path / "store-link"
    try:
        linked_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows account")

    with pytest.raises(IntegrityError, match="Symlink or junction"):
        ExperimentStore(linked_root)
    assert sentinel.read_bytes() == b"keep"


def test_registered_artifact_link_guard_is_exercised_without_windows_link_privilege(
    tmp_path: Path,
    source_artifacts: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, _, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )
    run_directory = store.runs_dir / hashlib.sha256(manifest.run_id.value.encode()).hexdigest()
    protected = run_directory / "strategy-source.py"
    outside = tmp_path / "outside-secret"
    outside.write_bytes(b"do-not-read-or-replace")
    real_is_symlink = Path.is_symlink

    def simulated_link(path: Path) -> bool:
        if path.absolute() == protected.absolute():
            return True
        return real_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", simulated_link)
    with pytest.raises(IntegrityError, match="Symlink or junction"):
        store.get(manifest.run_id)
    assert outside.read_bytes() == b"do-not-read-or-replace"


def _repeat_runner(
    manifest: ExperimentManifest,
    artifacts_path: Path,
    dataset_store: DatasetStore,
    *,
    quantity_override: Decimal | None = None,
) -> ReplayOutcome:
    config = manifest.config
    strategy_bytes = (artifacts_path / "strategy-source.py").read_bytes()
    project_bytes = (artifacts_path / "source-bundle.zip").read_bytes()
    strategy_hash = hashlib.sha256(strategy_bytes).hexdigest()
    project_hash = hashlib.sha256(project_bytes).hexdigest()
    if strategy_bytes != STRATEGY_SOURCE.read_bytes():
        raise IntegrityError("Test adapter has no registered implementation for this source")
    snapshot = dataset_store.load_snapshot(config.datasets[0].snapshot_id)
    if snapshot.content_hash != config.datasets[0].content_hash:
        raise IntegrityError("Test adapter resolved a different dataset snapshot")
    settings = config.backtest_settings
    execution_values = config.execution_assumptions
    commission_values = execution_values["commission"]
    slippage_values = execution_values["slippage"]
    quantity = quantity_override or Decimal(str(config.parameters["quantity"]))
    engine = BacktestEngine(
        dataset_store,
        (snapshot,),
        BuyAndHold(snapshot.instrument.instrument_id, quantity),
        BacktestConfig(
            initial_cash=config.initial_capital,
            currency=config.currency,
            max_order_notional=Decimal(str(settings["max_order_notional"])),
            allow_short=bool(settings["allow_short"]),
            short_assumption=(
                str(settings["short_assumption"])
                if settings["short_assumption"] is not None
                else None
            ),
            allow_zero_costs=bool(settings["allow_zero_costs"]),
        ),
        ExecutionConfig(
            maximum_participation=Decimal(str(execution_values["maximum_participation"])),
            commission=PerUnitCommission(
                Decimal(str(commission_values["per_unit_rate"])),
                None
                if commission_values["minimum"] is None
                else Decimal(str(commission_values["minimum"])),
            ),
            slippage=ConfiguredSlippage(
                SlippageKind(str(slippage_values["kind"])),
                Decimal(str(slippage_values["value"])),
            ),
            zero_cost_assumption=(
                str(execution_values["zero_cost_assumption"])
                if execution_values["zero_cost_assumption"] is not None
                else None
            ),
        ),
    )
    result = engine.run(
        start=config.start,
        end=config.end,
        benchmark=InstrumentId(str(config.benchmark["instrument_id"]))
        if config.benchmark is not None
        else None,
    )
    return ReplayOutcome(
        result,
        strategy_hash,
        project_hash,
        config.datasets,
        manifest.input_fingerprint,
    )


def test_repeat_rebuilds_m5_engine_from_verified_manifest_and_keeps_both_runs(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, dataset_store, _, original_result, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )

    repeated = store.verify_repeat(manifest.run_id, _repeat_runner, dataset_store)

    assert repeated.run_id != manifest.run_id
    assert repeated.input_fingerprint == manifest.input_fingerprint
    assert repeated.result_fingerprint == manifest.result_fingerprint
    assert len(store.search()) == 2
    assert store.get(manifest.run_id).result_fingerprint == result_fingerprint(original_result)
    assert dict(repeated.runtime_metadata)["repeat_of"] == manifest.run_id.value
    assert dict(repeated.runtime_metadata)["repeat_status"] == "matched"
    assert dict(repeated.runtime_metadata)["omitted_artifacts"] == ("result-summary.txt",)
    assert "result-summary.txt" not in {item.name for item in repeated.artifacts}


def test_repeat_mismatch_is_retained_under_a_new_run_id(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, dataset_store, _, _, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )

    def changed_runner(
        original: ExperimentManifest, artifacts_path: Path, snapshots: DatasetStore
    ) -> ReplayOutcome:
        return _repeat_runner(original, artifacts_path, snapshots, quantity_override=Decimal("11"))

    with pytest.raises(RepeatMismatchError) as caught:
        store.verify_repeat(manifest.run_id, changed_runner, dataset_store)

    repeated = store.get(caught.value.run_id)
    assert repeated.run_id != manifest.run_id
    assert repeated.result_fingerprint != manifest.result_fingerprint
    assert dict(repeated.runtime_metadata)["repeat_status"] == "mismatched"
    assert dict(repeated.runtime_metadata)["omitted_artifacts"] == ("result-summary.txt",)
    assert len(store.search()) == 2
    report_bundle = zipfile.ZipFile(
        io.BytesIO(store.read_artifact(repeated.run_id, "report-bundle.zip"))
    )
    report_json = json.loads(report_bundle.read("report.json"))
    nav_table = next(table for table in report_json["tables"] if table["name"] == "nav")
    assert nav_table["rows"][-1][1] != str(Decimal("10001.5"))


@pytest.mark.parametrize("mismatch", ["dataset", "commission"])
def test_initial_result_cannot_be_registered_under_different_inputs(
    tmp_path: Path, source_artifacts: dict[str, bytes], mismatch: str
) -> None:
    _, snapshot, result = _run(tmp_path / "dataset")
    base = _manifest(snapshot, result, source_artifacts)
    config = base.config
    if mismatch == "dataset":
        wrong_dataset = replace(config.datasets[0], snapshot_id="1" * 64)
        config = replace(config, datasets=(wrong_dataset,))
    else:
        assumptions = dict(config.execution_assumptions)
        assumptions["commission_model_repr"] = repr(PerUnitCommission(Decimal("0.2")))
        config = replace(config, execution_assumptions=assumptions)
    wrong_manifest = replace(
        base,
        run_id=RunId(f"wrong-{mismatch}"),
        config=config,
    )
    wrong_outcome = _outcome(wrong_manifest, result, source_artifacts)
    store = ExperimentStore(tmp_path / "experiments")

    with pytest.raises(IntegrityError, match="metadata|identity|inputs"):
        store.create(wrong_manifest, wrong_outcome, source_artifacts)
    assert store.search() == ()


def test_repeat_rejects_source_attestation_mismatch_before_creating_run(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    store, dataset_store, _, _, manifest = _record(
        tmp_path / "experiments", tmp_path / "dataset", source_artifacts
    )

    def wrong_source(
        original: ExperimentManifest, artifacts_path: Path, snapshots: DatasetStore
    ) -> ReplayOutcome:
        return replace(
            _repeat_runner(original, artifacts_path, snapshots),
            strategy_source_hash="0" * 64,
        )

    with pytest.raises(IntegrityError, match="identities"):
        store.verify_repeat(manifest.run_id, wrong_source, dataset_store)
    assert len(store.search()) == 1


def test_optimization_integration_registers_each_m5_m6_m7_trial_in_m8(
    tmp_path: Path, source_artifacts: dict[str, bytes]
) -> None:
    from quant_terminal.optimization import (
        Direction,
        OptimizationJournal,
        OptimizationSpec,
        ParameterDimension,
        SearchMethod,
        TrialEvaluation,
        run_optimization,
    )

    registry = ExperimentStore(tmp_path / "optimization-registry")
    journal = OptimizationJournal(
        tmp_path / "optimization-journal.sqlite3",
        OptimizationSpec(
            "integration-optimization",
            {
                "source_hash": hashlib.sha256(source_artifacts["strategy-source.py"]).hexdigest(),
                "dataset_source": str(SOURCE_DATA.name),
                "interval": [START.isoformat(), END.isoformat()],
            },
            (ParameterDimension("quantity", (Decimal("2"), Decimal("5"))),),
            SearchMethod.GRID,
            2,
            42,
            "trade_count",
            Direction.MAXIMIZE,
        ),
    )
    spec = journal.spec

    def evaluate(index: int, parameters: dict[str, object]) -> TrialEvaluation:
        _dataset, snapshot, result = _run(
            tmp_path / f"dataset-{index}", quantity=Decimal(str(parameters["quantity"]))
        )
        manifest = _manifest(
            snapshot,
            result,
            source_artifacts,
            run_id=f"optimization-{index}",
            parameters={"quantity": Decimal(str(parameters["quantity"]))},
        )
        report_directory = export_bundle(build_report(result), tmp_path / f"report-{index}")
        report_stream = io.BytesIO()
        with zipfile.ZipFile(report_stream, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(report_directory.rglob("*")):
                if path.is_file():
                    archive.writestr(
                        path.relative_to(report_directory).as_posix(), path.read_bytes()
                    )
        shutil.rmtree(report_directory)
        stored = registry.create(
            manifest,
            _outcome(manifest, result, source_artifacts),
            {**source_artifacts, "report-bundle.zip": report_stream.getvalue()},
        )
        return TrialEvaluation(
            dict(stored.metrics),
            stored.run_id.value,
            stored.input_fingerprint,
            stored.result_fingerprint,
        )

    result = run_optimization(spec, evaluate, journal.record)
    assert result.completed and len(result.trials) == 2
    assert all(trial.status == "eligible" for trial in result.trials)
    assert all(registry.get(trial.run_id).run_id.value == trial.run_id for trial in result.trials)
    assert all(registry.read_artifact(trial.run_id, "report-bundle.zip") for trial in result.trials)
    assert len(journal.load()) == 2


def test_walk_forward_optimizes_only_training_bounds_and_stitches_registered_oos(
    tmp_path: Path,
    source_artifacts: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quant_terminal.backtesting.strategies import StrategyContext
    from quant_terminal.data.model import Bar
    from quant_terminal.optimization import (
        Direction,
        OptimizationJournal,
        OptimizationSpec,
        ParameterDimension,
        SearchMethod,
        TrialEvaluation,
        run_optimization,
    )
    from quant_terminal.walk_forward import (
        FrozenSelection,
        OOSRun,
        WalkForwardSpec,
        WindowMode,
        run_walk_forward,
    )

    strategy_source = b"""class FlatResearchFixture:
    name = "walk_forward_flat"
    def on_bar(self, context, bar):
        return ()
"""
    with zipfile.ZipFile(io.BytesIO(source_artifacts["source-bundle.zip"])) as source_zip:
        project_files = {
            item.filename: source_zip.read(item.filename) for item in source_zip.infolist()
        }
    project_files["examples/walk_forward_flat.py"] = strategy_source
    project_stream = io.BytesIO()
    with zipfile.ZipFile(project_stream, "w", compression=zipfile.ZIP_STORED) as project_zip:
        for name, content in sorted(project_files.items()):
            item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.compress_type = zipfile.ZIP_STORED
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            item.internal_attr = 0
            item.extra = b""
            item.comment = b""
            project_zip.writestr(item, content)
    source_artifacts = {
        **source_artifacts,
        "source-bundle.zip": project_stream.getvalue(),
        "strategy-source.py": strategy_source,
    }

    class FlatResearchFixture:
        name = "walk_forward_flat"

        def on_bar(self, context: StrategyContext, bar: Bar) -> tuple:
            return ()

    registry, data_store, snapshot, _full_result, base_manifest = _record(
        tmp_path / "wf-registry", tmp_path / "wf-dataset", source_artifacts
    )
    training_start = START - timedelta(days=1)
    fixed_config = base_manifest.config.to_dict()
    fixed_config.pop("start")
    fixed_config.pop("end")
    fixed_config.pop("parameters")
    fixed_config["strategy_id"] = "test.walk-forward-flat.v1"
    fixed_config["strategy_name"] = "walk_forward_flat"
    spec = WalkForwardSpec(
        "wf-integration",
        {"experiment_config": fixed_config},
        (
            DatasetIdentity(
                snapshot.snapshot_id,
                snapshot.content_hash,
                snapshot.input_hash,
                snapshot.row_count,
                (INSTRUMENT.instrument_id.value,),
            ),
        ),
        hashlib.sha256(source_artifacts["strategy-source.py"]).hexdigest(),
        training_start,
        END,
        timedelta(days=1),
        timedelta(days=1),
        timedelta(days=1),
        WindowMode.ROLLING,
    )
    phase = {"name": ""}
    query_ends = []
    original_query = DatasetStore.query_bars

    def instrumented_query(
        self: DatasetStore,
        selected_snapshot: DatasetSnapshot,
        *,
        start: datetime | None = None,
        end: datetime,
    ) -> tuple:
        query_ends.append((phase["name"], end))
        if phase["name"] in {"training", "oos"}:
            assert end <= phase["end"]
        return original_query(self, selected_snapshot, start=start, end=end)

    monkeypatch.setattr(DatasetStore, "query_bars", instrumented_query)

    def run_interval(
        start: datetime,
        end: datetime,
        quantity: Decimal = Decimal("10"),
        initial_cash: Decimal = Decimal("10000"),
    ) -> BacktestResult:
        engine = BacktestEngine(
            data_store,
            (snapshot,),
            FlatResearchFixture(),
            BacktestConfig(initial_cash, "USD", max_order_notional=Decimal("1000000")),
            ExecutionConfig(commission=PerUnitCommission(Decimal("0.1"))),
        )
        return engine.run(start=start, end=end, benchmark=INSTRUMENT.instrument_id)

    def registered_manifest(
        run_id: str,
        result: BacktestResult,
        start: datetime,
        end: datetime,
        quantity: Decimal = Decimal("10"),
        initial_cash: Decimal = Decimal("10000"),
    ) -> ExperimentManifest:
        base = _manifest(snapshot, result, source_artifacts, run_id=run_id)
        manifest = replace(
            base,
            config=replace(
                base.config,
                strategy_id="test.walk-forward-flat.v1",
                strategy_name="walk_forward_flat",
                strategy_source_hash=hashlib.sha256(strategy_source).hexdigest(),
                start=start,
                end=end,
                parameters={"quantity": quantity},
                initial_capital=initial_cash,
            ),
            result_fingerprint=result_fingerprint(result),
            metrics=analyze(result, AnalyticsConfig()).metrics,
        )
        report_directory = export_bundle(build_report(result), tmp_path / f"report-{run_id}")
        report_stream = io.BytesIO()
        with zipfile.ZipFile(report_stream, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(report_directory.rglob("*")):
                if path.is_file():
                    archive.writestr(
                        path.relative_to(report_directory).as_posix(), path.read_bytes()
                    )
        shutil.rmtree(report_directory)
        artifacts = {**source_artifacts, "report-bundle.zip": report_stream.getvalue()}
        return registry.create(manifest, _outcome(manifest, result, artifacts), artifacts)

    def optimize(training) -> FrozenSelection:
        assert training.start == training_start + timedelta(days=training.index)
        assert training.end == training_start + timedelta(days=training.index + 1)
        assert not hasattr(training, "oos_start") and not hasattr(training, "oos_end")
        phase["name"] = "training"
        phase["end"] = training.end
        optimization_spec = OptimizationSpec(
            f"wf-m9-campaign-{training.index}",
            {
                "walk_forward_fingerprint": training.spec_fingerprint,
                "window_index": training.index,
                "training_interval": [training.start.isoformat(), training.end.isoformat()],
                "dataset_content_hash": snapshot.content_hash,
            },
            (ParameterDimension("quantity", (Decimal("10"),)),),
            SearchMethod.GRID,
            1,
            17,
            "trade_count",
            Direction.MAXIMIZE,
        )
        journal = OptimizationJournal(
            tmp_path / f"wf-m9-journal-{training.index}.sqlite3", optimization_spec
        )

        def evaluate(_index: int, parameters: dict[str, object]) -> TrialEvaluation:
            quantity = Decimal(str(parameters["quantity"]))
            is_result = run_interval(training.start, training.end, quantity)
            stored = registered_manifest(
                f"wf-selected-{training.index}",
                is_result,
                training.start,
                training.end,
                quantity,
            )
            return TrialEvaluation(
                dict(stored.metrics),
                stored.run_id.value,
                stored.input_fingerprint,
                stored.result_fingerprint,
            )

        optimization = run_optimization(optimization_spec, evaluate, journal.record)
        assert optimization.completed and len(journal.load()) == 1
        assert optimization.ranked_eligible_trials
        trial = optimization.ranked_eligible_trials[0]
        selected = registry.get(RunId(trial.run_id))
        assert registry.read_artifact(selected.run_id, "report-bundle.zip")
        return FrozenSelection(
            optimization.optimization_id, optimization.specification_fingerprint, selected
        )

    def execute_oos(window, selection) -> OOSRun:
        assert selection.selected_manifest.run_id.value == f"wf-selected-{window.index}"
        phase["name"] = "oos"
        phase["end"] = window.oos_end
        initial_cash = (
            Decimal("10000") if window.index == 0 else saved[-1].oos_run.result.nav[-1].nav
        )
        oos_result = run_interval(
            window.oos_start,
            window.oos_end,
            Decimal(str(selection.parameters["quantity"])),
            initial_cash,
        )
        manifest = registered_manifest(
            f"wf-oos-{window.index}",
            oos_result,
            window.oos_start,
            window.oos_end,
            Decimal(str(selection.parameters["quantity"])),
            initial_cash,
        )
        assert registry.read_artifact(manifest.run_id, "report-bundle.zip")
        return OOSRun(manifest, result_fingerprint(oos_result), oos_result)

    saved = []
    result = run_walk_forward(spec, optimize, execute_oos, saved.append)
    assert result.completed and len(result.windows) == 3
    training_queries = [end for name, end in query_ends if name == "training"]
    oos_queries = [end for name, end in query_ends if name == "oos"]
    assert len(training_queries) == 3
    assert oos_queries and all(end <= END for end in oos_queries)
    assert any(point.timestamp == START for point in result.stitched_oos_equity)
    assert all(START <= point.timestamp < END for point in result.stitched_oos_equity)
    assert len(saved) == 3
    assert [row.oos_run.manifest.run_id.value for row in result.windows] == [
        "wf-oos-0",
        "wf-oos-1",
        "wf-oos-2",
    ]
    assert all(
        registry.get(row.oos_run.manifest.run_id).run_id == row.oos_run.manifest.run_id
        for row in result.windows
    )
    for index, row in enumerate(result.windows):
        assert row.oos_run.result.nav[-1].positions == ()
        assert row.oos_run.manifest.config.initial_capital == (
            Decimal("10000") if index == 0 else result.windows[index - 1].oos_run.result.nav[-1].nav
        )
        assert all(
            point.equity == nav.nav
            for point, nav in zip(
                (item for item in result.stitched_oos_equity if item.window_index == index),
                row.oos_run.result.nav,
                strict=True,
            )
        )
    from quant_terminal.accounting.ledger import PositionSnapshot
    from quant_terminal.walk_forward.engine import stitch_oos_equity

    first_row = result.windows[0]
    open_position = PositionSnapshot(
        INSTRUMENT.instrument_id,
        INSTRUMENT.symbol,
        Decimal("1"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal(0),
        Decimal(0),
    )
    bad_nav = replace(first_row.oos_run.result.nav[-1], positions=(open_position,))
    bad_result = replace(
        first_row.oos_run.result,
        nav=first_row.oos_run.result.nav[:-1] + (bad_nav,),
    )
    bad_run = OOSRun(first_row.oos_run.manifest, result_fingerprint(bad_result), bad_result)
    with pytest.raises(DomainError, match="residual positions"):
        stitch_oos_equity((replace(first_row, oos_run=bad_run),))
    from quant_terminal.walk_forward.engine import _check_result_times

    oos_result = result.windows[0].oos_run.result
    if oos_result.fills and oos_result.orders and oos_result.orders[0].events:
        fill = oos_result.fills[0]
        delayed = replace(
            fill,
            timestamp=START + timedelta(hours=4),
            observed_at=START + timedelta(hours=6),
        )
        earlier_trade = replace(
            fill,
            timestamp=START + timedelta(hours=2),
            observed_at=START + timedelta(hours=7),
        )
        original_order = oos_result.orders[0]
        older_pending_order = replace(
            original_order,
            events=tuple(
                replace(event, timestamp=START + timedelta(hours=1, seconds=event.sequence))
                for event in original_order.events
            ),
        )
        delayed_publication_result = replace(
            oos_result,
            fills=(delayed, earlier_trade),
            orders=(original_order, older_pending_order),
        )
        _check_result_times(delayed_publication_result, result.windows[0].window)
    altered = dict(fixed_config)
    altered["currency"] = "EUR"
    bad_spec = replace(spec, identity={"experiment_config": altered})
    with pytest.raises(DomainError, match="currency differs"):
        run_walk_forward(
            bad_spec,
            lambda _: result.windows[0].selection,
            lambda _window, _selection: result.windows[0].oos_run,
            lambda _row: None,
        )


def test_walk_forward_oos_adapter_liquidates_at_last_close_and_fails_without_capacity(
    tmp_path: Path,
) -> None:
    from quant_terminal.backtesting.strategies import StrategyContext
    from quant_terminal.core.enums import OrderStatus
    from quant_terminal.core.identifiers import OrderId
    from quant_terminal.data.model import Bar
    from quant_terminal.orders.models import OrderIntent, Side, TradeType

    class ScheduledFlatten:
        name = "scheduled_flatten"

        def on_bar(self, context: StrategyContext, bar: Bar) -> tuple[OrderIntent, ...]:
            if bar.start >= datetime(2026, 11, 25, 14, 32, tzinfo=UTC):
                return ()
            quantity = context.position(INSTRUMENT.instrument_id)
            side = Side.BUY if quantity == 0 else Side.SELL
            if context.has_active_order(INSTRUMENT.instrument_id):
                return ()
            return (
                OrderIntent(
                    OrderId(f"scheduled-{bar.start.strftime('%Y%m%d%H%M%S')}-{side.value}"),
                    INSTRUMENT,
                    side,
                    TradeType.MARKET,
                    Decimal("10") if side is Side.BUY else quantity,
                    context.current_time,
                    max_execution_price=Decimal("200") if side is Side.BUY else None,
                ),
            )

    def snapshot_for(directory: Path, final_volume: str) -> tuple[DatasetStore, DatasetSnapshot]:
        store = DatasetStore(directory / "datasets")
        source = directory / "bars.csv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "instrument_id,frequency,start,end,available_at,open,high,low,close,volume,source\n"
            "ETF-1,1m,2026-11-25T14:30:00+00:00,2026-11-25T14:31:00+00:00,2026-11-25T14:31:00+00:00,100.00,100.10,99.90,100.00,1000,fixture\n"
            "ETF-1,1m,2026-11-25T14:31:00+00:00,2026-11-25T14:32:00+00:00,2026-11-25T14:32:00+00:00,100.50,100.60,100.40,100.50,1000,fixture\n"
            f"ETF-1,1m,2026-11-25T14:32:00+00:00,2026-11-25T14:33:00+00:00,2026-11-25T14:33:00+00:00,101.00,101.10,100.90,101.00,{final_volume},fixture\n",
            encoding="utf-8",
        )
        return store, store.import_csv(source, instrument=INSTRUMENT, source="fixture")

    start = datetime(2026, 11, 25, 14, 30, tzinfo=UTC)
    end = datetime(2026, 11, 26, 14, 30, tzinfo=UTC)
    execution = ExecutionConfig(
        maximum_participation=Decimal("0.01"),
        commission=PerUnitCommission(Decimal("0.1")),
    )

    def verify_liquidated(result: BacktestResult) -> None:
        if result.nav[-1].positions or any(
            order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
            for order in result.orders
        ):
            raise DomainError("No executable final-close liquidation capacity")

    data_store, snapshot = snapshot_for(tmp_path / "executable", "1000")
    successful = BacktestEngine(
        data_store,
        (snapshot,),
        ScheduledFlatten(),
        BacktestConfig(Decimal("10000"), "USD"),
        execution,
    ).run(start=start, end=end, benchmark=INSTRUMENT.instrument_id)
    assert len(successful.fills) == 2
    assert successful.fills[-1].side is Side.SELL
    final_bar = data_store.query_bars(snapshot, start=start, end=end)[-1]
    assert successful.fills[-1].timestamp == final_bar.start
    assert successful.fills[-1].reference_price == final_bar.close
    assert successful.commission_cost == Decimal("2.0")
    assert successful.nav[-1].positions == ()
    verify_liquidated(successful)

    blocked_store, blocked_snapshot = snapshot_for(tmp_path / "blocked", "0")
    blocked = BacktestEngine(
        blocked_store,
        (blocked_snapshot,),
        ScheduledFlatten(),
        BacktestConfig(Decimal("10000"), "USD"),
        execution,
    ).run(start=start, end=end, benchmark=INSTRUMENT.instrument_id)
    with pytest.raises(DomainError, match="liquidation capacity"):
        verify_liquidated(blocked)
