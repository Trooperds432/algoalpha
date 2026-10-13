"""Search identity, candidate and trial retention tests."""

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from quant_terminal.analytics.metrics import Metric
from quant_terminal.core.exceptions import DomainError
from quant_terminal.optimization import (
    Bound,
    Direction,
    MetricConstraint,
    OptimizationJournal,
    OptimizationSpec,
    ParameterDimension,
    SearchMethod,
    TrialEvaluation,
    TrialExecutionError,
    enumerate_candidates,
    metric_distribution,
    neighbor_metric_changes,
    parameter_distribution,
    parameter_surface,
    response_matrix,
    run_optimization,
    score_metrics,
)


def _spec(method: SearchMethod = SearchMethod.GRID) -> OptimizationSpec:
    return OptimizationSpec(
        "campaign-1",
        {"source": "a" * 64, "data": {"snapshot": "b" * 64}},
        (
            ParameterDimension("window", (2, 4)),
            ParameterDimension("threshold", (Decimal("0.1"), Decimal("0.2"))),
        ),
        method,
        4 if method is SearchMethod.GRID else 3,
        17,
        "sharpe",
        Direction.MAXIMIZE,
        (MetricConstraint("maximum_drawdown", Bound.MAXIMUM, Decimal("0.25")),),
    )


def test_grid_order_fingerprint_and_deep_identity_freezing() -> None:
    source = {"nested": {"snapshot": "b" * 64}}
    spec = OptimizationSpec(
        "grid",
        source,
        (ParameterDimension("x", (1, 2)),),
        SearchMethod.GRID,
        2,
        0,
        "sharpe",
        Direction.MAXIMIZE,
    )
    fingerprint = spec.fingerprint
    source["nested"]["snapshot"] = "changed"
    assert [dict(x) for x in enumerate_candidates(spec)] == [{"x": 1}, {"x": 2}]
    assert "bbbb" in str(spec.base_identity)
    assert spec.fingerprint == fingerprint


def test_decimal_parameter_and_spec_identity_ignore_ambient_precision() -> None:
    precise = Decimal("1.123456789012345678901234567890123456789")
    fingerprints = []
    choices = []
    for precision in (28, 8):
        with localcontext() as context:
            context.prec = precision
            dimension = ParameterDimension("precise", (precise,))
            spec = OptimizationSpec(
                "precision-independent",
                {"source": "f" * 64},
                (dimension,),
                SearchMethod.GRID,
                1,
                0,
                "sharpe",
                Direction.MAXIMIZE,
                (MetricConstraint("maximum_drawdown", Bound.MAXIMUM, precise),),
            )
            choices.append(dimension.choices)
            fingerprints.append(spec.fingerprint)
    assert choices[0] == choices[1] == (str(precise),)
    assert fingerprints[0] == fingerprints[1]


def test_random_candidates_repeat_without_replacement_and_large_space() -> None:
    dims = tuple(ParameterDimension(f"d{i}", tuple(range(10))) for i in range(8))
    spec = OptimizationSpec(
        "random",
        {"source": "c" * 64},
        dims,
        SearchMethod.RANDOM,
        100,
        999,
        "sortino",
        Direction.MAXIMIZE,
    )
    left, right = enumerate_candidates(spec), enumerate_candidates(spec)
    assert left == right
    assert len(left) == len({tuple(sorted(row.items())) for row in left}) == 100


def test_grid_preflight_budget_and_invalid_spaces_fail_before_execution() -> None:
    with pytest.raises(DomainError, match="exceeds candidate_budget"):
        OptimizationSpec(
            "too-many",
            {"source": "a" * 64},
            (ParameterDimension("x", tuple(range(5))),),
            SearchMethod.GRID,
            4,
            0,
            "sharpe",
            Direction.MAXIMIZE,
        )
    with pytest.raises(DomainError, match="unique"):
        ParameterDimension("dup", (Decimal("1"), Decimal("1.0")))


def test_score_constraints_inclusive_and_unavailable_metrics_explain_failure() -> None:
    spec = _spec()
    metrics = {
        "sharpe": Metric(Decimal("1.2"), "ratio", 100),
        "maximum_drawdown": Metric(Decimal("0.25"), "fraction", 100),
    }
    assert score_metrics(metrics, spec).eligible
    metrics["maximum_drawdown"] = Metric(None, "fraction", 0, "unavailable", "no NAV")
    score = score_metrics(metrics, spec)
    assert not score.eligible
    assert "no NAV" in score.reasons[0]


def test_optimizer_retains_success_infeasible_failure_and_resumes() -> None:
    spec = _spec()
    recorded = []

    def evaluate(index: int, params: dict[str, object]) -> TrialEvaluation:
        if index == 1:
            raise TrialExecutionError("invalid parameter for this data")
        drawdown = Decimal("0.30") if index == 2 else Decimal("0.10")
        return TrialEvaluation(
            {
                "sharpe": Metric(Decimal(index), "ratio", 10),
                "maximum_drawdown": Metric(drawdown, "fraction", 10),
            },
            f"run-{index}",
            "d" * 64,
            "e" * 64,
        )

    result = run_optimization(spec, evaluate, recorded.append)
    assert [trial.status for trial in result.trials] == [
        "eligible",
        "failed",
        "infeasible",
        "eligible",
    ]
    assert len(recorded) == 4
    assert [x.index for x in result.ranked_eligible_trials] == [3, 0]
    assert result.trials[0].to_dict()["specification_fingerprint"] == spec.fingerprint
    assert json.loads(json.dumps(result.trials[0].to_dict()))["index"] == 0
    surface = parameter_surface(result.trials, "sharpe", ("window", "threshold"))
    assert len(surface) == 4
    assert len(response_matrix(surface, "window", "threshold")) == 3
    assert len(neighbor_metric_changes(surface)) == 2
    distribution = metric_distribution(result.trials, "sharpe")
    assert distribution.failed_count == 1
    assert distribution.unavailable_count == 0
    assert (
        sum(count for _, count in parameter_distribution(result.trials, "window").frequencies) == 4
    )
    prefix = result.trials[:2]
    resumed = run_optimization(spec, evaluate, recorded.append, start_index=2, existing=prefix)
    assert resumed.completed and len(resumed.trials) == 4


def test_sqlite_journal_resumes_verifies_and_rejects_spec_mismatch(tmp_path) -> None:
    spec = _spec()
    journal = OptimizationJournal(tmp_path / "campaign.sqlite3", spec)

    def evaluate(index: int, params: dict[str, object]) -> TrialEvaluation:
        return TrialEvaluation(
            {"sharpe": Metric(Decimal(index), "ratio", 10)},
            f"journal-run-{index}",
            "a" * 64,
            "b" * 64,
        )

    partial = run_optimization(
        spec, evaluate, journal.record, cancelled=lambda: len(journal.load()) >= 2
    )
    assert not partial.completed and len(journal.load()) == 2
    fabricated = replace(partial.trials[0], parameters={"window": "bogus"})
    with pytest.raises(DomainError, match="candidate index"):
        journal.record(fabricated)
    with pytest.raises(DomainError, match="different optimization spec"):
        OptimizationJournal(
            tmp_path / "campaign.sqlite3",
            OptimizationSpec(
                "campaign-1",
                {"source": "z" * 64},
                _spec().dimensions,
                SearchMethod.GRID,
                4,
                17,
                "sharpe",
                Direction.MAXIMIZE,
                _spec().constraints,
            ),
        )
    with sqlite3.connect(journal.path) as connection:
        connection.execute("DROP TRIGGER trials_no_update")
        connection.execute("UPDATE trials SET payload=? WHERE candidate_index=0", (b"{}",))
    from quant_terminal.optimization import JournalIntegrityError

    with pytest.raises(JournalIntegrityError, match="checksum"):
        journal.load()


def test_numeric_neighbor_and_surface_order_is_numeric() -> None:
    one_dimensional = OptimizationSpec(
        "numeric-neighbors",
        {"source": "e" * 64},
        (ParameterDimension("x", (1, 2, 10)),),
        SearchMethod.GRID,
        3,
        0,
        "sharpe",
        Direction.MAXIMIZE,
    )

    def evaluate(index: int, params: dict[str, object]) -> TrialEvaluation:
        return TrialEvaluation(
            {"sharpe": Metric(Decimal(str(params["x"])), "ratio", 1)},
            f"numeric-{index}",
            "c" * 64,
            "d" * 64,
        )

    results = run_optimization(one_dimensional, evaluate, lambda _: None)
    changes = neighbor_metric_changes(parameter_surface(results.trials, "sharpe", ("x",)))
    assert changes == (Decimal(1), Decimal(8))

    two_dimensional = OptimizationSpec(
        "numeric-surface",
        {"source": "e" * 64},
        (ParameterDimension("x", (1, 2, 10)), ParameterDimension("y", ("a", "b"))),
        SearchMethod.GRID,
        6,
        0,
        "sharpe",
        Direction.MAXIMIZE,
    )
    surface_trials = run_optimization(two_dimensional, evaluate, lambda _: None).trials
    points = parameter_surface(surface_trials, "sharpe", ("x", "y"))
    matrix = response_matrix(points, "x", "y")
    assert matrix[0] == ("y", 1, 2, 10)


def test_identity_or_storage_errors_fail_campaign_closed() -> None:
    spec = _spec()
    recorded = []

    def evaluate(index: int, params: dict[str, object]) -> TrialEvaluation:
        raise DomainError("M8 identity mismatch")

    with pytest.raises(DomainError, match="identity mismatch"):
        run_optimization(spec, evaluate, recorded.append)
    assert not recorded


def test_cancellation_preserves_contiguous_prefix() -> None:
    spec = _spec()
    recorded = []
    count = 0

    def evaluate(index: int, params: dict[str, object]) -> TrialEvaluation:
        return TrialEvaluation(
            {
                "sharpe": Metric(Decimal(index), "ratio", 2),
                "maximum_drawdown": Metric(Decimal("0.1"), "fraction", 2),
            },
            f"run-{index}",
            "f" * 64,
            "1" * 64,
        )

    def cancel() -> bool:
        nonlocal count
        count += 1
        return count > 2

    partial = run_optimization(spec, evaluate, recorded.append, cancelled=cancel)
    assert not partial.completed
    assert len(partial.trials) == 2
    resumed = run_optimization(
        spec, evaluate, recorded.append, start_index=2, existing=partial.trials
    )
    assert resumed.completed and len(resumed.trials) == 4
