"""Native-currency FIFO accounting, explicit FX and simple carrying costs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from quant_terminal.core.exceptions import DomainError

ZERO = Decimal(0)
ONE = Decimal(1)


def amount(value: object, name: str, *, minimum: Decimal | None = None) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError(f"{name} must be a finite Decimal")
    parts = value.as_tuple()
    if len(parts.digits) > 34 or abs(int(parts.exponent)) > 18:
        raise DomainError(f"{name} exceeds precision/scale bounds")
    if minimum is not None and value < minimum:
        raise DomainError(f"{name} must be at least {minimum}")
    return value


def currency(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z]{3}", value) is None:
        raise DomainError("Currency must be three uppercase ASCII letters")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80 or not value.isprintable():
        raise DomainError(f"{name} must be bounded nonempty printable text")
    return value


def _mapping(values: dict[str, Decimal], name: str) -> dict[str, Decimal]:
    if not isinstance(values, dict) or not 1 <= len(values) <= 64:
        raise DomainError(f"{name} must be a bounded currency mapping")
    return {currency(key): amount(value, name) for key, value in values.items()}


def fx_conversion(
    source_currency: str,
    target_currency: str,
    quantity: Decimal,
    rate: Decimal,
    fee: Decimal = ZERO,
) -> dict[str, object]:
    source, target = currency(source_currency), currency(target_currency)
    q, r, cost = (
        amount(quantity, "quantity"),
        amount(rate, "rate"),
        amount(fee, "fee", minimum=ZERO),
    )
    if source == target or q <= ZERO or r <= ZERO:
        raise DomainError("FX requires distinct currencies, positive quantity and conversion rate")
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        return {
            "source_currency": source,
            "target_currency": target,
            "source_debit": q + cost,
            "target_credit": q * r,
            "quote_convention": "target units per source unit",
            "fee_currency": source,
        }


def borrow_availability(requested: Decimal, available: Decimal) -> dict[str, object]:
    requested = amount(requested, "requested", minimum=ZERO)
    available = amount(available, "available", minimum=ZERO)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        return {
            "approved": requested <= available,
            "requested": requested,
            "available": available,
            "shortfall": max(ZERO, requested - available),
        }


def _accrual(annual_rate: Decimal, days: Decimal, day_count: Decimal) -> Decimal:
    rate = amount(annual_rate, "annual_rate", minimum=ZERO)
    elapsed = amount(days, "days", minimum=ZERO)
    basis = amount(day_count, "day_count")
    if basis not in (Decimal(360), Decimal(365), Decimal(366)):
        raise DomainError("Supported day counts are 360, 365 and 366")
    return rate * elapsed / basis


def borrow_fee(
    short_quantity: Decimal,
    price: Decimal,
    annual_rate: Decimal,
    days: Decimal,
    day_count: Decimal = Decimal(365),
) -> dict[str, object]:
    q = amount(short_quantity, "short_quantity", minimum=ZERO)
    p = amount(price, "price")
    if p <= ZERO:
        raise DomainError("Borrow mark price must be positive")
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        fee = q * p * _accrual(annual_rate, days, day_count)
        return {
            "borrow_fee": fee,
            "short_market_value": q * p,
            "convention": "simple annual accrual; charge in instrument currency",
        }


def financing(
    cash_balance: Decimal,
    debit_rate: Decimal,
    credit_rate: Decimal,
    days: Decimal,
    day_count: Decimal = Decimal(365),
) -> dict[str, object]:
    cash = amount(cash_balance, "cash_balance")
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        debit = _accrual(debit_rate, days, day_count)
        credit = _accrual(credit_rate, days, day_count)
        charge = max(ZERO, -cash) * debit
        income = max(ZERO, cash) * credit
        return {
            "debit_interest": charge,
            "credit_interest": income,
            "cash_change": income - charge,
            "convention": "simple annual accrual",
        }


@dataclass(slots=True)
class _Lot:
    quantity: Decimal
    price: Decimal


def multi_currency_account(
    base_currency: str,
    initial_balances: dict[str, Decimal],
    fx_rates: dict[str, Decimal],
    events: tuple[dict[str, object], ...],
) -> dict[str, object]:
    """Isolated chronological replay; rates are base units per native unit.

    Trade fees expense immediately in native realised PnL. Explicit cash events
    are external flows. Financing and borrow charges use an expense event. No
    approved credit line, settlement-lag or broker account is implied.
    """
    base = currency(base_currency)
    balances = _mapping(initial_balances, "initial_balances")
    rates = _mapping(fx_rates, "fx_rates")
    if rates.get(base) != ONE or any(value <= ZERO for value in rates.values()):
        raise DomainError("FX quotes must be positive and base currency quote must equal one")
    if set(balances) - set(rates):
        raise DomainError("Missing initial currency quote")
    if not isinstance(events, tuple) or len(events) > 256:
        raise DomainError("Account replay requires at most 256 ordered events")
    lots: dict[str, list[_Lot]] = {}
    assets: dict[str, str] = {}
    marks: dict[str, Decimal] = {}
    realised: dict[str, Decimal] = {}
    seen: set[str] = set()
    journal: list[dict[str, object]] = []
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        initial_nav = sum((value * rates[code] for code, value in balances.items()), ZERO)
        flows = ZERO
        for event in events:
            if not isinstance(event, dict):
                raise DomainError("Every account event must be an object")
            identity = _text(event.get("event_id"), "event_id")
            kind = event.get("kind")
            if identity in seen:
                raise DomainError("Duplicate account event ID")
            seen.add(identity)
            if kind == "fx":
                expected = {
                    "event_id",
                    "kind",
                    "source_currency",
                    "target_currency",
                    "quantity",
                    "rate",
                    "fee",
                }
                if set(event) != expected:
                    raise DomainError("FX event has missing or unsupported fields")
                source, target = (
                    currency(event["source_currency"]),
                    currency(event["target_currency"]),
                )
                if source not in rates or target not in rates:
                    raise DomainError("Missing currency quote")
                q, r = amount(event["quantity"], "quantity"), amount(event["rate"], "rate")
                fee = amount(event["fee"], "fee", minimum=ZERO)
                conversion = fx_conversion(source, target, q, r, fee)
                if balances.get(source, ZERO) < q + fee:
                    raise DomainError("FX conversion exceeds native source cash")
                balances[source] = balances.get(source, ZERO) - q - fee
                balances[target] = balances.get(target, ZERO) + q * r
                journal.append({"event_id": identity, "kind": kind, **conversion})
            elif kind in ("cash", "expense"):
                if set(event) != {"event_id", "kind", "currency", "quantity"}:
                    raise DomainError("Cash/expense event has unsupported fields")
                code = currency(event["currency"])
                q = amount(event["quantity"], "quantity")
                if code not in rates or (kind == "expense" and q < ZERO):
                    raise DomainError("Missing quote or negative expense")
                change = q if kind == "cash" else -q
                balances[code] = balances.get(code, ZERO) + change
                if kind == "cash":
                    flows += change * rates[code]
                else:
                    realised[code] = realised.get(code, ZERO) - q
                journal.append(
                    {"event_id": identity, "kind": kind, "currency": code, "cash_change": change}
                )
            elif kind == "fx_rate":
                if set(event) != {"event_id", "kind", "currency", "rate"}:
                    raise DomainError("FX mark event has unsupported fields")
                code, r = currency(event["currency"]), amount(event["rate"], "rate")
                if r <= ZERO or (code == base and r != ONE) or code not in rates:
                    raise DomainError("Invalid FX rate mark")
                rates[code] = r
                journal.append({"event_id": identity, "kind": kind, "currency": code, "rate": r})
            elif kind == "mark":
                if set(event) != {"event_id", "kind", "asset_id", "price"}:
                    raise DomainError("Asset mark event has unsupported fields")
                asset, price = _text(event["asset_id"], "asset_id"), amount(event["price"], "price")
                if asset not in assets or price < ZERO:
                    raise DomainError("Unknown asset or negative mark")
                marks[asset] = price
                journal.append(
                    {"event_id": identity, "kind": kind, "asset_id": asset, "price": price}
                )
            elif kind == "trade":
                required = {
                    "event_id",
                    "kind",
                    "asset_id",
                    "currency",
                    "quantity",
                    "price",
                    "fee",
                    "borrow_available",
                    "asset_class",
                }
                if set(event) != required:
                    raise DomainError("Trade event has missing or unsupported fields")
                if event["asset_class"] not in ("equity", "etf"):
                    raise DomainError(
                        "Native FIFO trades support equity/ETF; use derivative modules"
                    )
                asset, code = _text(event["asset_id"], "asset_id"), currency(event["currency"])
                q, price = amount(event["quantity"], "quantity"), amount(event["price"], "price")
                fee = amount(event["fee"], "fee", minimum=ZERO)
                available = amount(event["borrow_available"], "borrow_available", minimum=ZERO)
                if (
                    code not in rates
                    or q == ZERO
                    or price <= ZERO
                    or assets.get(asset, code) != code
                ):
                    raise DomainError("Invalid trade asset/currency/quantity/price")
                positions = lots.setdefault(asset, [])
                old_quantity = sum((lot.quantity for lot in positions), ZERO)
                if max(ZERO, -(old_quantity + q)) > available and q < ZERO:
                    raise DomainError("Short trade exceeds explicit borrow availability")
                assets[asset], marks[asset] = code, price
                remaining, pnl = q, -fee
                while positions and remaining * positions[0].quantity < ZERO:
                    lot = positions[0]
                    closed = min(abs(remaining), abs(lot.quantity))
                    sign = ONE if lot.quantity > ZERO else -ONE
                    pnl += closed * sign * (price - lot.price)
                    lot.quantity -= closed * sign
                    remaining += closed * sign
                    if lot.quantity == ZERO:
                        positions.pop(0)
                if remaining != ZERO:
                    positions.append(_Lot(remaining, price))
                realised[code] = realised.get(code, ZERO) + pnl
                balances[code] = balances.get(code, ZERO) - q * price - fee
                journal.append(
                    {
                        "event_id": identity,
                        "kind": kind,
                        "asset_id": asset,
                        "currency": code,
                        "quantity": q,
                        "native_realised_pnl": pnl,
                    }
                )
            else:
                raise DomainError("Unsupported account event kind")
        cash_nav = sum((value * rates[code] for code, value in balances.items()), ZERO)
        positions_report: list[dict[str, object]] = []
        asset_nav = ZERO
        for asset, position in lots.items():
            quantity = sum((lot.quantity for lot in position), ZERO)
            value = quantity * marks[asset]
            unrealised = sum(((marks[asset] - lot.price) * lot.quantity for lot in position), ZERO)
            base_value = value * rates[assets[asset]]
            asset_nav += base_value
            positions_report.append(
                {
                    "asset_id": asset,
                    "currency": assets[asset],
                    "quantity": quantity,
                    "mark": marks[asset],
                    "native_market_value": value,
                    "native_unrealised_pnl": unrealised,
                    "base_market_value": base_value,
                }
            )
        nav = cash_nav + asset_nav
        return {
            "base_currency": base,
            "native_cash": balances,
            "fx_rates": rates,
            "positions": tuple(positions_report),
            "native_realised_pnl": realised,
            "initial_base_nav": initial_nav,
            "external_flows_base": flows,
            "base_cash_nav": cash_nav,
            "base_asset_nav": asset_nav,
            "base_nav": nav,
            "total_base_pnl": nav - initial_nav - flows,
            "journal": tuple(journal),
            "convention": "base/native FX quotes; FIFO; trade fees expensed immediately",
        }
