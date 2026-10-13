"""Unsupported methods preserve complete selection evidence without invented numbers."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from quant_terminal.analytics import Metric
from quant_terminal.core.exceptions import DomainError
from quant_terminal.optimization import (
    Direction,
    OptimizationSpec,
    ParameterDimension,
    SearchMethod,
    TrialEvaluation,
    TrialExecutionError,
    run_optimization,
)
from quant_terminal.robustness.method_support import method_support_report


def campaign():
    spec = OptimizationSpec(
        "diagnostic-population",
        {"source": "a" * 64, "dataset": "b" * 64},
        (ParameterDimension("window", (2, 3, 4)),),
        SearchMethod.GRID,
        3,
        9,
        "sharpe",
        Direction.MAXIMIZE,
    )

    def evaluate(index, parameters):
        if index == 2:
            raise TrialExecutionError("strategy callback failed")
        metric = (
            Metric(Decimal(2), "ratio", 100)
            if index == 0
            else Metric(None, "ratio", 1, "unavailable", "insufficient history")
        )
        return TrialEvaluation({"sharpe": metric}, f"run-{index}", "c" * 64, "d" * 64)

    return spec, run_optimization(spec, evaluate, lambda trial: None)


def test_all_attempts_and_prerequisites_retained_without_numeric_statistics() -> None:
    spec, result = campaign()
    report = method_support_report(spec, result)
    assert dict(report.status_counts) == {"eligible": 1, "infeasible": 1, "failed": 1}
    assert len(report.trial_identities) == len(set(report.trial_identities)) == 3
    artifact = json.loads(report.artifact_bytes())
    assert artifact["report_fingerprint"] == report.fingerprint
    trials = artifact["report"]["trials"]
    assert [row["trial"]["index"] for row in trials] == [0, 1, 2]
    assert trials[-1]["trial"]["error"] == "TrialExecutionError: strategy callback failed"
    assert trials[-1]["trial"]["run_id"] is None
    methods = artifact["report"]["methods"]
    assert {method["method"] for method in methods} == {
        "deflated_sharpe_ratio",
        "white_reality_check",
        "purged_embargoed_cross_validation",
    }
    assert all(
        method["status"] == "unavailable" and method["statistic"] is None for method in methods
    )
    assert all(len(method["required_evidence"]) >= 3 for method in methods)
    assert "Raw candidate count" in methods[0]["design"]


def test_censored_foreign_or_incomplete_trial_populations_reject() -> None:
    spec, result = campaign()
    with pytest.raises(DomainError, match="completed"):
        method_support_report(spec, replace(result, completed=False))
    with pytest.raises(DomainError, match="Complete trial population"):
        method_support_report(spec, replace(result, trials=result.trials[:1]))
    duplicate = (result.trials[0], result.trials[0], result.trials[2])
    with pytest.raises(DomainError, match="candidate enumeration"):
        method_support_report(spec, replace(result, trials=duplicate))
    foreign = (replace(result.trials[0], parameters={"window": 999}), *result.trials[1:])
    with pytest.raises(DomainError, match="candidate enumeration"):
        method_support_report(spec, replace(result, trials=foreign))


def test_population_identity_ignores_elapsed_time_but_binds_failed_evidence() -> None:
    spec, result = campaign()
    original = method_support_report(spec, result)
    retimed = replace(
        result, trials=tuple(replace(t, elapsed_seconds=Decimal(99)) for t in result.trials)
    )
    assert method_support_report(spec, retimed).artifact_bytes() == original.artifact_bytes()
    changed = replace(
        result, trials=(*result.trials[:2], replace(result.trials[2], error="different"))
    )
    assert (
        method_support_report(spec, changed).population_fingerprint
        != original.population_fingerprint
    )
