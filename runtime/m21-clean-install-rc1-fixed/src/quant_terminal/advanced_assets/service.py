"""Validated asset-specific JSON seam with no execution or network transport."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext

from quant_terminal.advanced_assets import futures, money, options
from quant_terminal.core.exceptions import DomainError

OPERATIONS: dict[str, Callable[..., object]] = {
    "multi_currency_account": money.multi_currency_account,
    "fx_conversion": money.fx_conversion,
    "borrow_availability": money.borrow_availability,
    "borrow_fee": money.borrow_fee,
    "financing": money.financing,
    "futures_pnl": futures.futures_pnl,
    "futures_margin": futures.futures_margin,
    "futures_settlement": futures.futures_settlement,
    "continuous_contract": futures.continuous_contract,
    "option_price": options.option_price,
    "option_payoff": options.option_payoff,
    "option_margin": options.option_margin,
}
TEXT = frozenset(
    {
        "base_currency",
        "source_currency",
        "target_currency",
        "currency",
        "asset_id",
        "asset_class",
        "event_id",
        "kind",
        "option_type",
        "style",
        "policy",
        "method",
    }
)


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise DomainError("Financial input must be a bounded decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise DomainError("Invalid decimal string") from exc
    parts = parsed.as_tuple()
    if not parsed.is_finite() or len(parts.digits) > 34 or abs(int(parts.exponent)) > 18:
        raise DomainError("Financial input exceeds finite precision/scale bounds")
    return parsed


def _input(value: object, key: str, depth: int = 0) -> object:
    if depth > 4:
        raise DomainError("Asset request nesting exceeds supported bounds")
    if key in TEXT or key == "contract_ids" and not isinstance(value, list):
        if not isinstance(value, str) or not 1 <= len(value) <= 80:
            raise DomainError("Expected bounded text parameter")
        return value
    if isinstance(value, list):
        if len(value) > 256 or not value and key != "events":
            raise DomainError("Asset input arrays exceed supported bounds")
        return tuple(_input(item, key, depth + 1) for item in value)
    if isinstance(value, dict):
        limit = 64 if key in {"initial_balances", "fx_rates"} else 20
        if not 1 <= len(value) <= limit or any(not isinstance(k, str) for k in value):
            raise DomainError("Expected bounded parameter mapping")
        if key in {"initial_balances", "fx_rates"}:
            return {k: _decimal(v) for k, v in value.items()}
        return {k: _input(v, k, depth + 1) for k, v in value.items()}
    return _decimal(value)


def capabilities() -> dict[str, object]:
    return {
        "mode": "local_asset_research",
        "operations": sorted(OPERATIONS),
        "execution_enabled": False,
        "legacy_backtest_support": "equity/ETF only",
        "contract_counts": "JSON integers",
        "financial_values": "decimal strings",
    }


def research(request: dict[str, object]) -> dict[str, object]:
    if set(request) != {"operation", "inputs"}:
        raise DomainError("Expected exactly operation and inputs")
    name, raw = request["operation"], request["inputs"]
    if not isinstance(name, str) or name not in OPERATIONS:
        raise DomainError("Unsupported advanced asset operation")
    if not isinstance(raw, dict) or any(not isinstance(k, str) for k in raw):
        raise DomainError("Asset inputs must be a parameter object")
    function = OPERATIONS[name]
    try:
        inspect.signature(function).bind(**raw)
    except TypeError as exc:
        raise DomainError("Missing or unsupported asset parameters") from exc
    arguments: dict[str, object] = {}
    for key, value in raw.items():
        if key == "contracts" or key == "multiplier" and name.startswith("option_"):
            if type(value) is not int or abs(value) > 1_000_000:
                raise DomainError("Contract count/option multiplier must be a bounded integer")
            arguments[key] = value
        else:
            arguments[key] = _input(value, key)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        result = function(**arguments)
    return {
        "schema_version": "m20.v1",
        "operation": name,
        "mode": "local_asset_research",
        "result": result,
        "assumptions": {
            "execution": "Native asset simulations only; no orders",
            "quotes": "FX conversion target/source; account translation base/native",
            "margin": "Supplied futures collateral or narrow single-leg equity option policy",
        },
    }
