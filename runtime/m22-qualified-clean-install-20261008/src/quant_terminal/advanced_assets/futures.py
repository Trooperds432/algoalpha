"""Bounded Decimal futures research calculations; no broker or execution hooks."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext

from quant_terminal.core.exceptions import DomainError

_PRECISION = 34
_MAX_EXPONENT = 18
_MAX_DIGITS = 34
_MAX_ROWS = 256


def _context() -> Context:
    """Return an isolated context, with caller flags and traps discarded."""
    context = Context(prec=_PRECISION, rounding=ROUND_HALF_EVEN)
    context.clear_flags()
    return context


def _decimal(
    value: object, name: str, *, nonnegative: bool = False, positive: bool = False
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError(f"{name} must be a finite Decimal")
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int) or abs(parts.exponent) > _MAX_EXPONENT:
        raise DomainError(f"{name} exponent is outside the supported range")
    if len(parts.digits) > _MAX_DIGITS:
        raise DomainError(f"{name} exceeds {_MAX_DIGITS} digits")
    if positive and value <= 0:
        raise DomainError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise DomainError(f"{name} must be nonnegative")
    return value


def _result(value: Decimal, name: str) -> Decimal:
    if not value.is_finite():
        raise DomainError(f"{name} is outside the supported Decimal range")
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int) or abs(parts.exponent) > _MAX_EXPONENT:
        raise DomainError(f"{name} exponent is outside the supported range")
    if len(parts.digits) > _MAX_DIGITS:
        raise DomainError(f"{name} exceeds {_MAX_DIGITS} digits")
    return value


def _contracts(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainError("contracts must be a signed integer")
    if abs(value) > 10**12:
        raise DomainError("contracts exceed the supported quantity bound")
    return value


def _parameters(
    contracts: object,
    multiplier: object,
    initial_margin_per_contract: object,
    maintenance_margin_per_contract: object,
) -> tuple[int, Decimal, Decimal, Decimal]:
    count = _contracts(contracts)
    mult = _decimal(multiplier, "multiplier", positive=True)
    initial = _decimal(initial_margin_per_contract, "initial margin", nonnegative=True)
    maintenance = _decimal(maintenance_margin_per_contract, "maintenance margin", nonnegative=True)
    if maintenance > initial:
        raise DomainError("maintenance margin cannot exceed initial margin")
    return count, mult, initial, maintenance


def _margin_amount(contracts: int, per_contract: Decimal, name: str) -> Decimal:
    try:
        with localcontext(_context()):
            result = Decimal(abs(contracts)) * per_contract
    except DecimalException as exc:
        raise DomainError(f"{name} is outside the supported Decimal range") from exc
    return _result(result, name)


def _difference(left: Decimal, right: Decimal, name: str) -> Decimal:
    try:
        with localcontext(_context()):
            difference = left - right
    except DecimalException as exc:
        raise DomainError(f"{name} is outside the supported Decimal range") from exc
    return _result(difference, name)


def futures_pnl(
    contracts: int,
    multiplier: Decimal,
    entry_price: Decimal,
    exit_price: Decimal,
) -> dict[str, object]:
    """Return mark-to-mark PnL: signed contracts × multiplier × price change."""
    count = _contracts(contracts)
    mult = _decimal(multiplier, "multiplier", positive=True)
    entry = _decimal(entry_price, "entry price")
    exit_ = _decimal(exit_price, "exit price")
    try:
        with localcontext(_context()):
            pnl = Decimal(count) * mult * (exit_ - entry)
    except DecimalException as exc:
        raise DomainError("futures PnL is outside the supported Decimal range") from exc
    pnl = _result(pnl, "futures PnL")
    return {
        "contracts": count,
        "multiplier": mult,
        "entry_price": entry,
        "exit_price": exit_,
        "pnl": pnl,
    }


def futures_margin(
    contracts: int,
    multiplier: Decimal,
    settlement_price: Decimal,
    initial_margin_per_contract: Decimal,
    maintenance_margin_per_contract: Decimal,
    equity: Decimal,
) -> dict[str, object]:
    """Report supplied per-contract margin requirements and any call; no top-up occurs."""
    count, mult, initial_rate, maintenance_rate = _parameters(
        contracts, multiplier, initial_margin_per_contract, maintenance_margin_per_contract
    )
    settlement = _decimal(settlement_price, "settlement price")
    cash_equity = _decimal(equity, "equity", nonnegative=True)
    initial = _margin_amount(count, initial_rate, "initial margin requirement")
    maintenance = _margin_amount(count, maintenance_rate, "maintenance margin requirement")
    breached = cash_equity < maintenance
    top_up = (
        max(_difference(initial, cash_equity, "margin top-up"), Decimal(0))
        if breached
        else Decimal(0)
    )
    return {
        "contracts": count,
        "multiplier": mult,
        "settlement_price": settlement,
        "equity": cash_equity,
        "initial_margin_required": initial,
        "maintenance_margin_required": maintenance,
        "maintenance_breached": breached,
        "margin_call_top_up_to_initial": top_up,
        "action_taken": False,
    }


def futures_settlement(
    contracts: int,
    multiplier: Decimal,
    settlement_prices: tuple[Decimal, ...],
    initial_cash: Decimal,
    initial_margin_per_contract: Decimal,
    maintenance_margin_per_contract: Decimal,
) -> dict[str, object]:
    """Bank daily variation from the first mark; margin-call diagnostics never add cash."""
    count, mult, initial_rate, maintenance_rate = _parameters(
        contracts, multiplier, initial_margin_per_contract, maintenance_margin_per_contract
    )
    if not isinstance(settlement_prices, tuple) or not 1 <= len(settlement_prices) <= _MAX_ROWS:
        raise DomainError(f"settlement_prices must be a tuple with 1..{_MAX_ROWS} rows")
    prices = tuple(
        _decimal(price, f"settlement_prices[{i}]") for i, price in enumerate(settlement_prices)
    )
    cash = _decimal(initial_cash, "initial cash", nonnegative=True)
    initial_required = _margin_amount(count, initial_rate, "initial margin requirement")
    maintenance_required = _margin_amount(count, maintenance_rate, "maintenance margin requirement")
    if cash < initial_required:
        raise DomainError("initial cash is insufficient for initial margin")

    rows: list[dict[str, object]] = []
    cumulative_variation = Decimal(0)
    for day, price in enumerate(prices):
        previous: Decimal | None = prices[day - 1] if day else None
        try:
            with localcontext(_context()):
                variation = (
                    Decimal(0) if previous is None else Decimal(count) * mult * (price - previous)
                )
                cash = cash + variation
                cumulative_variation = cumulative_variation + variation
        except DecimalException as exc:
            raise DomainError(
                "daily futures settlement is outside the supported Decimal range"
            ) from exc
        variation = _result(variation, f"daily variation[{day}]")
        cash = _result(cash, f"cash[{day}]")
        cumulative_variation = _result(cumulative_variation, "cumulative variation")
        breached = cash < maintenance_required
        top_up = (
            max(_difference(initial_required, cash, f"margin top-up[{day}]"), Decimal(0))
            if breached
            else Decimal(0)
        )
        rows.append(
            {
                "day": day,
                "previous_settlement": previous,
                "settlement_price": price,
                "variation": variation,
                "cash": cash,
                "maintenance_breached": breached,
                "margin_call_top_up_to_initial": top_up,
                "action_taken": False,
            }
        )
    return {
        "contracts": count,
        "multiplier": mult,
        "initial_cash": _decimal(initial_cash, "initial cash", nonnegative=True),
        "initial_margin_required": initial_required,
        "maintenance_margin_required": maintenance_required,
        "daily_settlements": tuple(rows),
        "final_cash": cash,
        "cumulative_variation": cumulative_variation,
        "notional_purchase_debit": Decimal(0),
    }


def continuous_contract(
    prices: tuple[Decimal, ...],
    contract_ids: tuple[str, ...],
    roll_old_prices: tuple[Decimal, ...],
    method: str = "difference",
) -> dict[str, object]:
    """Build a non-tradable additive research series from explicit same-time roll quotes.

    `roll_old_prices[i]` is the expiring contract's quote at the timestamp of a roll
    into `contract_ids[i]`; it must be zero for all other rows and the initial row.
    Historical values are adjusted by the sum of subsequent new-minus-old roll gaps.
    """
    if method != "difference":
        raise DomainError("only difference back-adjustment is supported")
    if not all(isinstance(value, tuple) for value in (prices, contract_ids, roll_old_prices)):
        raise DomainError("prices, contract_ids, and roll_old_prices must be tuples")
    n = len(prices)
    if not 1 <= n <= _MAX_ROWS or len(contract_ids) != n or len(roll_old_prices) != n:
        raise DomainError(f"continuous-contract inputs must have equal lengths from 1..{_MAX_ROWS}")
    raw_prices = tuple(_decimal(price, f"prices[{i}]") for i, price in enumerate(prices))
    old_prices = tuple(
        _decimal(price, f"roll_old_prices[{i}]") for i, price in enumerate(roll_old_prices)
    )
    if any(
        not isinstance(identifier, str) or not identifier or len(identifier) > 128
        for identifier in contract_ids
    ):
        raise DomainError("contract IDs must be nonempty strings of at most 128 characters")
    rolls: list[dict[str, object]] = []
    gaps: dict[int, Decimal] = {}
    if old_prices[0] != 0:
        raise DomainError("initial row cannot contain an expiring-contract quote")
    for index in range(1, n):
        if contract_ids[index] != contract_ids[index - 1]:
            gap = _difference(raw_prices[index], old_prices[index], f"roll gap[{index}]")
            gaps[index] = gap
            rolls.append(
                {
                    "index": index,
                    "from_contract": contract_ids[index - 1],
                    "to_contract": contract_ids[index],
                    "old_contract_price": old_prices[index],
                    "new_contract_price": raw_prices[index],
                    "gap": gap,
                }
            )
        elif old_prices[index] != 0:
            raise DomainError("non-roll rows must have zero roll_old_prices")

    adjusted = list(raw_prices)
    cumulative = Decimal(0)
    try:
        with localcontext(_context()):
            for index in range(n - 1, -1, -1):
                adjusted[index] = _result(
                    raw_prices[index] + cumulative, f"adjusted_prices[{index}]"
                )
                if index in gaps:
                    cumulative += gaps[index]
    except DecimalException as exc:
        raise DomainError(
            "continuous-contract adjustment is outside the supported Decimal range"
        ) from exc
    return {
        "method": method,
        "contract_ids": contract_ids,
        "raw_prices": raw_prices,
        "adjusted_prices": tuple(adjusted),
        "rolls": tuple(rolls),
        "tradable": False,
    }
