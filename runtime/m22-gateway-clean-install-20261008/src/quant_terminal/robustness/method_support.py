"""Evidence-bound prerequisite reports for unimplemented statistical methods.

This design surface deliberately returns no statistic. A complete search ledger
is necessary for selection diagnostics, but does not establish independent trials.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import canonical_hash, canonical_json
from quant_terminal.optimization.engine import OptimizationResult
from quant_terminal.optimization.models import OptimizationSpec, enumerate_candidates


@dataclass(frozen=True, slots=True)
class MethodPrerequisites:
    method: str
    status: str
    required_evidence: tuple[str, ...]
    design: str

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "status": self.status,
            "statistic": None,
            "required_evidence": self.required_evidence,
            "design": self.design,
        }


@dataclass(frozen=True, slots=True)
class MethodSupportReport:
    """Immutable canonical population records, including unsuccessful attempts."""

    specification_bytes: bytes
    trial_records: tuple[bytes, ...]
    trial_identities: tuple[str, ...]
    status_counts: tuple[tuple[str, int], ...]
    population_fingerprint: str
    methods: tuple[MethodPrerequisites, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "scope": "prerequisite_and_method_design_only",
            "specification": json.loads(self.specification_bytes),
            "trials": [json.loads(record) for record in self.trial_records],
            "trial_identities": self.trial_identities,
            "status_counts": dict(self.status_counts),
            "population_fingerprint": self.population_fingerprint,
            "methods": [method.to_dict() for method in self.methods],
        }

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.to_dict())

    def artifact_bytes(self) -> bytes:
        return canonical_json({"report": self.to_dict(), "report_fingerprint": self.fingerprint})


def method_support_report(
    spec: OptimizationSpec, result: OptimizationResult
) -> MethodSupportReport:
    """Bind the entire M9 candidate population to explicit refusal diagnostics.

    Failed candidates have no M8 run identity; their canonical trial identity binds
    the campaign fingerprint and candidate index. Partial/censored campaigns reject.
    An independent trial count is never inferred from raw candidate count.
    """
    if not isinstance(spec, OptimizationSpec) or not isinstance(result, OptimizationResult):
        raise DomainError("Typed M9 specification and result are required")
    if (
        not result.completed
        or result.optimization_id != spec.optimization_id
        or result.specification_fingerprint != spec.fingerprint
        or result.direction != spec.direction.value
    ):
        raise DomainError("A completed, matching M9 campaign is required")
    expected = enumerate_candidates(spec)
    if len(result.trials) != len(expected):
        raise DomainError("Complete trial population required, including failed/noneligible trials")
    trials = sorted(result.trials, key=lambda trial: trial.index)
    records: list[bytes] = []
    identities: list[str] = []
    for index, (trial, parameters) in enumerate(zip(trials, expected, strict=True)):
        if (
            trial.index != index
            or trial.specification_fingerprint != spec.fingerprint
            or canonical_json(trial.parameters) != canonical_json(parameters)
        ):
            raise DomainError("Trial population does not match the complete candidate enumeration")
        record = trial.to_dict()
        # Wall time is retained by M9, not part of deterministic economic identity.
        record.pop("elapsed_seconds")
        identity = canonical_hash({"specification": spec.fingerprint, "trial_index": index})
        identities.append(identity)
        records.append(canonical_json({"trial_identity": identity, "trial": record}))
    population = {
        "specification": spec.to_dict(),
        "specification_fingerprint": spec.fingerprint,
        "trials": [json.loads(record) for record in records],
    }
    methods = (
        MethodPrerequisites(
            "deflated_sharpe_ratio",
            "unavailable",
            (
                "Verified compatible dated return series for every attempted candidate",
                "Independently justified and validated dependence correction "
                "for the effective independent trial count",
                "Validated expected-maximum Sharpe benchmark model and distributional assumptions",
                "Validated finite-sample Sharpe moments and selection-rule compatibility",
            ),
            "Use the full attempted population and explicit selection rule to construct a "
            "dependence-adjusted expected-maximum Sharpe benchmark, then evaluate its "
            "probabilistic Sharpe statistic. Raw candidate count cannot replace that correction.",
        ),
        MethodPrerequisites(
            "white_reality_check",
            "unavailable",
            (
                "Verified synchronized common-frequency "
                "candidate-versus-benchmark loss differentials",
                "Named null hypothesis, centering convention, and selection statistic",
                "Validated joint dependence-preserving bootstrap and block-length contract",
                "Reference null/alternative acceptance fixtures and multiplicity accounting",
            ),
            "Jointly resample centered loss differentials across the complete candidate family "
            "and compare the observed best performance statistic against the null maximum. "
            "Scalar Sharpe values cannot reconstruct the required differential series.",
        ),
        MethodPrerequisites(
            "purged_embargoed_cross_validation",
            "unavailable",
            (
                "Typed point-in-time feature and label information intervals",
                "Explicit temporal folds and independently chosen embargo interval",
                "Training-only fitting/transform contract with label-overlap purging",
                "Leakage fixtures covering boundary overlaps and preprocessing state",
            ),
            "Purge training labels whose information intervals overlap a validation fold; "
            "apply the declared embargo, fit transforms only on retained training observations, "
            "and preserve all fold assignments and excluded intervals as evidence.",
        ),
    )
    counts = Counter(trial.status for trial in trials)
    return MethodSupportReport(
        canonical_json({"fingerprint": spec.fingerprint, "configuration": spec.to_dict()}),
        tuple(records),
        tuple(identities),
        tuple((status, counts[status]) for status in ("eligible", "infeasible", "failed")),
        canonical_hash(population),
        methods,
    )
