"""Strict JSON seam shared by the local research CLI and HTTP API."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext

from quant_terminal.core.exceptions import DomainError
from quant_terminal.portfolio_research import allocation, diagnostics

OPERATIONS: dict[str, Callable[..., object]] = {
    "volatility_target": allocation.volatility_target,
    "risk_parity": allocation.risk_parity,
    "mean_variance": allocation.mean_variance,
    "black_litterman": allocation.black_litterman,
    "risk_contributions": allocation.risk_contributions,
    "factor_exposures": diagnostics.factor_exposures,
    "factor_attribution": diagnostics.factor_attribution,
    "factor_covariance": diagnostics.factor_covariance,
    "stress_scenarios": diagnostics.stress_scenarios,
    "correlation_regimes": diagnostics.correlation_regimes,
    "concentration": diagnostics.concentration,
    "liquidity_capacity": diagnostics.liquidity_capacity,
}


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 80:
        raise DomainError("Financial inputs must be bounded decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise DomainError("Invalid decimal string") from exc
    parts = result.as_tuple()
    if (
        not result.is_finite()
        or len(parts.digits) > 34
        or not isinstance(parts.exponent, int)
        or abs(parts.exponent) > 100
    ):
        raise DomainError("Decimal exceeds finite precision/scale bounds")
    return result


def _input(value: object, depth: int = 0) -> object:
    if isinstance(value, list):
        if depth >= 2 or not value or len(value) > 64:
            raise DomainError("Input arrays require 1..64 entries and at most two dimensions")
        return tuple(_input(item, depth + 1) for item in value)
    return _decimal(value)


def capabilities() -> dict[str, object]:
    return {
        "mode": "local_research",
        "operations": sorted(OPERATIONS),
        "financial_input": "decimal strings",
        "covariance_frequency": "annual for allocation",
        "execution_enabled": False,
    }


def research(request: dict[str, object]) -> dict[str, object]:
    if set(request) != {"operation", "inputs"}:
        raise DomainError("Expected exactly operation and inputs")
    name, raw = request["operation"], request["inputs"]
    if not isinstance(name, str) or name not in OPERATIONS:
        raise DomainError("Unsupported portfolio research operation")
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise DomainError("inputs must be a parameter object")
    function = OPERATIONS[name]
    try:
        inspect.signature(function).bind(**raw)
    except TypeError as exc:
        raise DomainError("Missing or unsupported research parameters") from exc
    arguments: dict[str, object] = {}
    for key, value in raw.items():
        if key == "max_iterations":
            if type(value) is not int or not 1 <= value <= 10000:
                raise DomainError("max_iterations must be an integer in 1..10000")
            arguments[key] = value
        else:
            arguments[key] = _input(value)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        result = function(**arguments)
    return {
        "schema_version": "m19.v1",
        "operation": name,
        "mode": "local_research",
        "result": result,
        "assumptions": {
            "alignment": "Input order defines asset and factor alignment",
            "returns": "fractions; annual expected excess returns/covariance for allocation",
            "execution": "Research output only; no orders or automatic allocations",
        },
    }
