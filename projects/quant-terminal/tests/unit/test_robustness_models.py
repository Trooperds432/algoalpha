"""Robustness identity, metric-delta and neighborhood fixtures."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext

import pytest

from quant_terminal.analytics import Metric
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import RunId
from quant_terminal.experiments import DatasetIdentity, ExperimentConfig, ExperimentManifest
from quant_terminal.optimization.engine import OptimizationTrial
from quant_terminal.robustness import (
    RobustnessScenario,
    RobustnessSpec,
    ScenarioKind,
    compare_scenario_metrics,
    parameter_neighborhood,
)
from quant_terminal.robustness.models import diagnose_plateau

START = datetime(2024, 1, 2, tzinfo=UTC)


def _manifest() -> ExperimentManifest:
    dataset = DatasetIdentity("a" * 64, "b" * 64, "c" * 64, 2, ("ETF-1",))
    config = ExperimentConfig(
        "strat",
        "Strategy",
        "d" * 64,
        "e" * 64,
        None,
        "0.0.1",
        "f" * 64,
        (dataset,),
        START,
        START + timedelta(days=2),
        "USD",
        Decimal("1000"),
        {"lookback": 3},
        7,
        {},
        {"annualization": 252},
        None,
        {"commission": {"kind": "per_unit", "rate": Decimal("0.01")}},
    )
    return ExperimentManifest(
        RunId("baseline-run"),
        START,
        config,
        "1" * 64,
        (("sharpe", Metric(Decimal("1"), "ratio", 30)),),
        "Python test",
        {},
    )


def _trial(
    index: int, parameter: int, metric: Decimal, status: str = "eligible"
) -> OptimizationTrial:
    return OptimizationTrial(
        index,
        "a" * 64,
        {"lookback": parameter},
        status,
        None,
        {"sharpe": Metric(metric, "ratio", 30)},
        f"run-{index}",
        "b" * 64,
        "c" * 64,
        Decimal(0),
    )


def test_scenario_identity_is_frozen_and_fingerprinted() -> None:
    changes = {"execution_assumptions.commission.rate": Decimal("0.02")}
    scenario = RobustnessScenario(
        "higher-cost", ScenarioKind.COMMISSION, tuple(changes.items()), "double fees"
    )
    spec = RobustnessSpec("robust-1", _manifest(), (scenario,))
    fingerprint = spec.fingerprint
    changes["execution_assumptions.commission.rate"] = Decimal("0.03")
    assert spec.scenarios[0].changes[0][1] == "0.02"
    assert spec.fingerprint == fingerprint


def test_scenario_rejects_protected_fields_and_mismatched_category() -> None:
    with pytest.raises(DomainError, match="protected"):
        RobustnessScenario("bad", ScenarioKind.PARAMETER, (("parameters.currency", "GBP"),), "bad")
    with pytest.raises(DomainError, match="category"):
        RobustnessScenario("bad", ScenarioKind.SLIPPAGE, (("parameters.lookback", 4),), "bad")
    with pytest.raises(DomainError, match="bar_delay"):
        RobustnessScenario("bad", ScenarioKind.BAR_DELAY, (("bar_delay", -1),), "negative")


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        (ScenarioKind.START_DATE, "startOops"),
        (ScenarioKind.END_DATE, "endOops"),
        (ScenarioKind.COMMISSION, "execution_assumptions.commissionFoo"),
        (ScenarioKind.SLIPPAGE, "execution_assumptions.slippageExtra"),
    ],
)
def test_scenario_paths_require_exact_field_boundaries(kind: ScenarioKind, path: str) -> None:
    with pytest.raises(DomainError, match="category"):
        RobustnessScenario("bad", kind, ((path, "changed"),), "not an allowed field")


def test_scenario_changes_deep_freeze_nested_json_values() -> None:
    nested = {"symbols": ["ETF-1", "ETF-2"]}
    scenario = RobustnessScenario(
        "regime", ScenarioKind.REGIME, (("regime.partition", nested),), "select regime"
    )
    fingerprint = RobustnessSpec("deep-freeze", _manifest(), (scenario,)).fingerprint
    nested["symbols"].append("ETF-3")
    stored = dict(scenario.changes)["regime.partition"]
    assert stored["symbols"] == ("ETF-1", "ETF-2")
    with pytest.raises(TypeError):
        stored["new"] = "mutation"
    assert RobustnessSpec("deep-freeze", _manifest(), (scenario,)).fingerprint == fingerprint


def test_scenario_metric_comparison_keeps_missing_and_zero_baseline_explicit() -> None:
    scenario = RobustnessScenario(
        "s", ScenarioKind.PARAMETER, (("parameters.lookback", 4),), "neighbor"
    )
    compared = compare_scenario_metrics(
        scenario,
        {
            "sharpe": Metric(Decimal(0), "ratio", 30),
            "cagr": Metric(None, "fraction", 0, "unavailable", "thin"),
        },
        {"sharpe": Metric(Decimal("0.2"), "ratio", 30)},
        run_id="run-2",
    )
    deltas = {item.metric: item for item in compared.deltas}
    assert deltas["sharpe"].absolute == Decimal("0.2")
    assert deltas["sharpe"].relative is None
    assert deltas["cagr"].absolute is None and deltas["cagr"].status == "unavailable"


def test_insufficient_sample_metric_values_are_not_compared_as_available() -> None:
    scenario = RobustnessScenario(
        "s", ScenarioKind.PARAMETER, (("parameters.lookback", 4),), "neighbor"
    )
    comparison = compare_scenario_metrics(
        scenario,
        {"var_95": Metric(Decimal("0.1"), "loss_fraction", 10, "insufficient_sample", "thin")},
        {"var_95": Metric(Decimal("0.08"), "loss_fraction", 10)},
        run_id="run-2",
    )
    delta = comparison.deltas[0]
    assert delta.status == "unavailable"
    assert delta.absolute is None and delta.reason == "thin"


def test_metric_deltas_ignore_ambient_decimal_context() -> None:
    scenario = RobustnessScenario(
        "s", ScenarioKind.PARAMETER, (("parameters.lookback", 4),), "neighbor"
    )
    baseline = {"return": Metric(Decimal("1.23456789"), "fraction", 30)}
    candidate = {"return": Metric(Decimal("1.34567891"), "fraction", 30)}
    expected = compare_scenario_metrics(scenario, baseline, candidate, run_id="x")
    old_precision = getcontext().prec
    try:
        getcontext().prec = 5
        actual = compare_scenario_metrics(scenario, baseline, candidate, run_id="x")
    finally:
        getcontext().prec = old_precision
    assert actual == expected


def test_numeric_parameter_neighborhood_exposes_narrow_peak_and_failed_level() -> None:
    trials = (
        _trial(0, 1, Decimal("0.5")),
        _trial(1, 2, Decimal("2.0")),
        _trial(2, 3, Decimal("0.4"), "failed"),
        _trial(3, 4, Decimal("0.6")),
    )
    rows = parameter_neighborhood(trials, "lookback", selected_value=2, metric="sharpe", radius=1)
    assert rows == (
        (1, Decimal("0.5"), "eligible"),
        (2, Decimal("2.0"), "eligible"),
        (3, None, "failed"),
    )


def test_parameter_neighborhood_requires_fixed_other_axes() -> None:
    trials = (
        OptimizationTrial(
            0,
            "a" * 64,
            {"lookback": 1, "threshold": 1},
            "eligible",
            None,
            {"sharpe": Metric(Decimal(1), "ratio", 30)},
            "a",
            "b" * 64,
            "c" * 64,
            Decimal(0),
        ),
        OptimizationTrial(
            1,
            "a" * 64,
            {"lookback": 1, "threshold": 2},
            "eligible",
            None,
            {"sharpe": Metric(Decimal(2), "ratio", 30)},
            "b",
            "b" * 64,
            "c" * 64,
            Decimal(0),
        ),
    )
    with pytest.raises(DomainError, match="exactly one trial"):
        parameter_neighborhood(trials, "lookback", selected_value=1, metric="sharpe")
    rows = parameter_neighborhood(
        trials,
        "lookback",
        selected_value=1,
        metric="sharpe",
        fixed_parameters={"threshold": 1},
    )
    assert rows == ((1, Decimal(1), "eligible"),)


def test_neighborhood_uses_declared_decimal_grid_levels_and_exposes_missing_cells() -> None:
    trials = (
        _trial(0, "0.10", Decimal("0.4")),
        _trial(1, "0.30", Decimal("0.9")),
        _trial(2, "0.40", Decimal("0.8")),
    )
    rows = parameter_neighborhood(
        trials,
        "lookback",
        selected_value=Decimal("0.30"),
        metric="sharpe",
        radius=1,
        declared_levels=(Decimal("0.10"), Decimal("0.20"), Decimal("0.30"), Decimal("0.40")),
    )
    assert rows == (
        (Decimal("0.20"), None, "missing"),
        (Decimal("0.30"), Decimal("0.9"), "eligible"),
        (Decimal("0.40"), Decimal("0.8"), "eligible"),
    )


def test_neighborhood_without_declared_levels_sorts_numeric_strings_numerically() -> None:
    trials = (
        _trial(0, "10", Decimal("0.5")),
        _trial(1, "2", Decimal("0.6")),
        _trial(2, "1", Decimal("0.7")),
    )
    rows = parameter_neighborhood(
        trials, "lookback", selected_value=Decimal("2"), metric="sharpe", radius=1
    )
    assert [row[0] for row in rows] == ["1", "2", "10"]


def test_neighborhood_rejects_mixed_campaigns_and_duplicate_trial_indices() -> None:
    first = _trial(0, 1, Decimal("0.5"))
    mixed = _trial(1, 2, Decimal("0.6"))
    mixed = OptimizationTrial(
        mixed.index,
        "b" * 64,
        mixed.parameters,
        mixed.status,
        mixed.score,
        mixed.metrics,
        mixed.run_id,
        mixed.input_fingerprint,
        mixed.result_fingerprint,
        mixed.elapsed_seconds,
        mixed.error,
    )
    with pytest.raises(DomainError, match="campaign identity"):
        parameter_neighborhood((first, mixed), "lookback", selected_value=1, metric="sharpe")
    with pytest.raises(DomainError, match="campaign identity"):
        parameter_neighborhood(
            (first, _trial(0, 2, Decimal("0.6"))),
            "lookback",
            selected_value=1,
            metric="sharpe",
        )


def test_plateau_diagnostic_distinguishes_broad_isolated_and_missing_neighborhoods() -> None:
    broad = diagnose_plateau(
        (
            (1, Decimal("0.95"), "eligible"),
            (2, Decimal("1.0"), "eligible"),
            (3, Decimal("0.96"), "eligible"),
        ),
        selected_value=2,
        threshold=Decimal("0.1"),
        direction="maximize",
    )
    assert broad.classification == "broad_plateau"
    assert broad.status_counts == (
        ("eligible", 3),
        ("failed", 0),
        ("infeasible", 0),
        ("unavailable", 0),
        ("missing", 0),
    )
    assert broad.stability_fraction == Decimal(1)

    isolated = diagnose_plateau(
        (
            (1, Decimal("0.2"), "eligible"),
            (2, Decimal("1.0"), "eligible"),
            (3, Decimal("0.3"), "eligible"),
        ),
        selected_value=2,
        threshold=Decimal("0.1"),
        direction="maximize",
    )
    assert isolated.classification == "isolated_optimum"
    assert isolated.plateau_levels == (2,)
    assert isolated.stability_fraction == Decimal(0)

    missed_optimum = diagnose_plateau(
        (
            (1, Decimal("2.0"), "eligible"),
            (2, Decimal("1.0"), "eligible"),
            (3, Decimal("2.0"), "eligible"),
        ),
        selected_value=2,
        threshold=Decimal("0.1"),
        direction="maximize",
    )
    assert missed_optimum.classification == "non_optimal_center"

    incomplete = diagnose_plateau(
        ((1, None, "missing"), (2, Decimal("1.0"), "eligible"), (3, Decimal("0.3"), "eligible")),
        selected_value=2,
        threshold=Decimal("0.1"),
        direction="maximize",
    )
    assert incomplete.classification == "inconclusive"
    assert incomplete.missing_cells == (1,)


def test_neighborhood_and_plateau_reject_nonfinite_metric_values() -> None:
    nan_trial = OptimizationTrial(
        0,
        "a" * 64,
        {"lookback": 1},
        "eligible",
        None,
        {"sharpe": Metric(Decimal("NaN"), "ratio", 30)},
        "run-0",
        "b" * 64,
        "c" * 64,
        Decimal(0),
    )
    with pytest.raises(DomainError, match="finite Decimals"):
        parameter_neighborhood((nan_trial,), "lookback", selected_value=1, metric="sharpe")
