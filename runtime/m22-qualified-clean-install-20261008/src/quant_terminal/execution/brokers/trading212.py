"""Trading 212 Demo wire mapping without an HTTP transport.

This is a pure protocol module: methods return request descriptions and parse
caller-supplied fixtures. No socket, URL opener, SDK, or environment lookup is
used here. A future transport must be separately authorized and reviewed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum

from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import OrderId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.orders.models import OrderIntent, Side, TradeType

DEMO_BASE_URL = "https://demo.trading212.com/api/v0"
API_VERSION = "v0-beta"
OPENAPI_SCHEMA_VERSION = "OpenAPI 3.0.1, Trading 212 API v0 Beta"
OPENAPI_SCHEMA_REVIEWED_DATE = "2026-10-02"
OPENAPI_SCHEMA_SOURCE = "https://docs.trading212.com/_bundle/api.json?download="
# Checked-in source record: OpenAPI endpoint descriptions at the source URL on the
# review date above. Rates are requests per period_seconds, not a scheduler policy.
OPENAPI_RATE_LIMIT_EVIDENCE: tuple[tuple[str, str, int, int], ...] = (
    ("GET", "/api/v0/equity/account/summary", 1, 5),
    ("GET", "/api/v0/equity/positions", 1, 1),
    ("GET", "/api/v0/equity/orders", 1, 5),
    ("GET", "/api/v0/equity/history/orders", 20, 60),
    ("POST", "/api/v0/equity/orders/limit", 1, 2),
)


class ProviderSchemaError(DomainError):
    """Provider data is missing, malformed, or outside the reviewed schema."""


@dataclass(frozen=True, slots=True)
class DemoCredentials:
    """Ephemeral Basic-auth material. Never persist or log this object."""

    key_id: str
    secret: str

    def __post_init__(self) -> None:
        for field, value in (("key_id", self.key_id), ("secret", self.secret)):
            if not isinstance(value, str) or not value or any(ch in value for ch in "\r\n:"):
                raise DomainError(f"{field} must be a nonempty Basic-auth-safe string")

    def __repr__(self) -> str:
        return "DemoCredentials(key_id=<redacted>, secret=<redacted>)"

    def basic_authorization(self) -> str:
        raw = f"{self.key_id}:{self.secret}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True, slots=True)
class RequestDescription:
    """Serializable wire shape for a scripted test transport only."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: tuple[tuple[str, object], ...] | None = None

    def __repr__(self) -> str:
        safe_headers = tuple(
            (key, "<redacted>" if key.lower() == "authorization" else value)
            for key, value in self.headers
        )
        return (
            f"RequestDescription(method={self.method!r}, url={self.url!r}, "
            f"headers={safe_headers!r}, body={self.body!r})"
        )


@dataclass(frozen=True, slots=True)
class EndpointBudget:
    """Published endpoint limit; local scheduler must still coordinate per account."""

    requests: int
    period: timedelta


ENDPOINT_BUDGETS: Mapping[str, EndpointBudget] = {
    "GET /equity/account/summary": EndpointBudget(1, timedelta(seconds=5)),
    "GET /equity/positions": EndpointBudget(1, timedelta(seconds=1)),
    "GET /equity/orders": EndpointBudget(1, timedelta(seconds=5)),
    "POST /equity/orders/limit": EndpointBudget(1, timedelta(seconds=2)),
    "GET /equity/history/orders": EndpointBudget(20, timedelta(minutes=1)),
}


class ProviderOrderStatus(StrEnum):
    LOCAL = "LOCAL"
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"
    NEW = "NEW"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    REPLACING = "REPLACING"
    REPLACED = "REPLACED"
    UNKNOWN = "UNKNOWN"


class SnapshotFreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    FUTURE = "future_timestamp"


@dataclass(frozen=True, slots=True)
class SnapshotFreshness:
    state: SnapshotFreshnessState
    observed_at: datetime
    checked_at: datetime
    age: timedelta
    blocked: bool


def assess_snapshot_freshness(
    observed_at: datetime, *, now: datetime, max_age: timedelta
) -> SnapshotFreshness:
    """Return a fail-closed heartbeat decision from caller-supplied timestamps."""
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise DomainError("Snapshot and check timestamps must be timezone-aware")
    if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
        raise DomainError("max_age must be a positive timedelta")
    observed = observed_at.astimezone(UTC)
    checked = now.astimezone(UTC)
    age = checked - observed
    if age < timedelta(0):
        return SnapshotFreshness(SnapshotFreshnessState.FUTURE, observed, checked, age, True)
    if age > max_age:
        return SnapshotFreshness(SnapshotFreshnessState.STALE, observed, checked, age, True)
    return SnapshotFreshness(SnapshotFreshnessState.FRESH, observed, checked, age, False)


@dataclass(frozen=True, slots=True)
class StatusMapping:
    provider: ProviderOrderStatus
    internal: OrderStatus | None
    blocked: bool


_STATUS_MAP: Mapping[ProviderOrderStatus, StatusMapping] = {
    ProviderOrderStatus.LOCAL: StatusMapping(
        ProviderOrderStatus.LOCAL, OrderStatus.SUBMITTED, True
    ),
    ProviderOrderStatus.UNCONFIRMED: StatusMapping(
        ProviderOrderStatus.UNCONFIRMED, OrderStatus.SUBMITTED, True
    ),
    ProviderOrderStatus.CONFIRMED: StatusMapping(
        ProviderOrderStatus.CONFIRMED, OrderStatus.ACCEPTED, False
    ),
    ProviderOrderStatus.NEW: StatusMapping(ProviderOrderStatus.NEW, OrderStatus.ACCEPTED, False),
    ProviderOrderStatus.CANCELLING: StatusMapping(
        ProviderOrderStatus.CANCELLING, OrderStatus.ACCEPTED, True
    ),
    ProviderOrderStatus.CANCELLED: StatusMapping(
        ProviderOrderStatus.CANCELLED, OrderStatus.CANCELLED, False
    ),
    ProviderOrderStatus.PARTIALLY_FILLED: StatusMapping(
        ProviderOrderStatus.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED, False
    ),
    ProviderOrderStatus.FILLED: StatusMapping(
        ProviderOrderStatus.FILLED, OrderStatus.FILLED, False
    ),
    ProviderOrderStatus.REJECTED: StatusMapping(
        ProviderOrderStatus.REJECTED, OrderStatus.REJECTED, False
    ),
    ProviderOrderStatus.REPLACING: StatusMapping(
        ProviderOrderStatus.REPLACING, OrderStatus.ACCEPTED, True
    ),
    ProviderOrderStatus.REPLACED: StatusMapping(ProviderOrderStatus.REPLACED, None, True),
    ProviderOrderStatus.UNKNOWN: StatusMapping(ProviderOrderStatus.UNKNOWN, None, True),
}

_ORDER_FIELDS = frozenset(
    {
        "createdAt",
        "currency",
        "extendedHours",
        "filledQuantity",
        "filledValue",
        "id",
        "initiatedFrom",
        "instrument",
        "limitPrice",
        "quantity",
        "side",
        "status",
        "stopPrice",
        "strategy",
        "ticker",
        "timeInForce",
        "type",
        "value",
    }
)
_FILL_FIELDS = frozenset(
    {"filledAt", "id", "price", "quantity", "tradingMethod", "type", "walletImpact"}
)
_ACCOUNT_FIELDS = frozenset({"cash", "currency", "id", "investments", "totalValue"})
_CASH_FIELDS = frozenset({"availableToTrade", "inPies", "reservedForOrders"})
_INVESTMENT_FIELDS = frozenset(
    {"currentValue", "realizedProfitLoss", "totalCost", "unrealizedProfitLoss"}
)
_POSITION_FIELDS = frozenset(
    {
        "averagePricePaid",
        "createdAt",
        "currentPrice",
        "instrument",
        "quantity",
        "quantityAvailableForTrading",
        "quantityInPies",
        "walletImpact",
    }
)
_POSITION_INSTRUMENT_FIELDS = frozenset({"currency", "isin", "name", "ticker"})
_POSITION_WALLET_FIELDS = frozenset(
    {"currency", "currentValue", "fxImpact", "totalCost", "unrealizedProfitLoss"}
)


@dataclass(frozen=True, slots=True)
class MappedProviderOrder:
    broker_order_id: str
    order_id: OrderId
    ticker: str
    instrument: InstrumentMeta
    side: Side
    status: StatusMapping
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderFillRecord:
    """A provider-supplied individual TRADE fill, retaining its broker identity."""

    broker_fill_id: str
    broker_order_id: str
    ticker: str
    side: Side
    quantity: Decimal
    price: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True, init=False)
class ProviderOrderFillAggregate:
    """Verified aggregate derived only from unique provider history fill records."""

    broker_order_id: str
    local_order_id: OrderId
    mapped_order: MappedProviderOrder
    ticker: str
    instrument: InstrumentMeta
    side: Side
    status: ProviderOrderStatus
    order_quantity: Decimal
    fills: tuple[ProviderFillRecord, ...]
    fill_ids: tuple[str, ...]
    filled_quantity: Decimal
    aggregate_id: str

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("Use the validated provider-history factory")

    @property
    def valid(self) -> bool:
        return self.verify_hash()

    def verify_hash(self) -> bool:
        expected = _provider_order_fill_aggregate_hash(
            self.broker_order_id,
            self.local_order_id,
            self.mapped_order,
            self.fills,
            self.filled_quantity,
        )
        return expected == self.aggregate_id

    @classmethod
    def from_parsed_history(
        cls,
        parsed_rows: Iterable[tuple[MappedProviderOrder, ProviderFillRecord]],
        *,
        expected_broker_order_id: str,
        expected_local_order: OrderIntent,
    ) -> ProviderOrderFillAggregate:
        """Create evidence after validating every parsed order and actual fill row."""
        if (
            not isinstance(expected_broker_order_id, str)
            or not expected_broker_order_id.isascii()
            or not expected_broker_order_id.isdigit()
            or len(expected_broker_order_id) > 19
            or int(expected_broker_order_id) <= 0
        ):
            raise DomainError("Expected broker order ID must be a positive decimal ID")
        if not isinstance(expected_local_order, OrderIntent):
            raise DomainError("Expected local validated OrderIntent is required")
        if (
            expected_local_order.order_type is not TradeType.LIMIT
            or expected_local_order.limit_price is None
        ):
            raise DomainError("Provider fill aggregation supports only local LIMIT intents")
        parsed: list[tuple[MappedProviderOrder, ProviderFillRecord]] = []
        for item in parsed_rows:
            if len(parsed) >= 10000:
                raise ProviderSchemaError("History aggregate exceeds the 10,000-fill safety bound")
            if not isinstance(item, tuple) or len(item) != 2:
                raise ProviderSchemaError("Aggregate requires parsed provider order/fill pairs")
            if not isinstance(item[0], MappedProviderOrder) or not isinstance(
                item[1], ProviderFillRecord
            ):
                raise ProviderSchemaError("Aggregate requires parsed provider order/fill pairs")
            parsed.append(item)
        if not parsed:
            raise ProviderSchemaError(
                "Cannot verify a fill aggregate without provider history rows"
            )
        first_order, _ = parsed[0]
        if first_order.broker_order_id != expected_broker_order_id:
            raise ProviderSchemaError("History does not match the expected broker order ID")
        local_binding = (
            first_order.instrument == expected_local_order.instrument
            and first_order.side is expected_local_order.side
            and first_order.quantity == expected_local_order.quantity
            and first_order.limit_price == expected_local_order.limit_price
        )
        if not local_binding:
            raise ProviderSchemaError(
                "Provider order does not match the expected local order intent"
            )

        by_fill_id: dict[str, ProviderFillRecord] = {}
        for order, fill in parsed:
            if order != first_order:
                raise ProviderSchemaError("History rows disagree on broker order identity or state")
            if (
                fill.broker_order_id != first_order.broker_order_id
                or fill.ticker != first_order.ticker
                or fill.side is not first_order.side
            ):
                raise ProviderSchemaError("Provider fill does not match its mapped broker order")
            if fill.broker_fill_id in by_fill_id:
                raise ProviderSchemaError("Provider history contains a duplicate fill ID")
            by_fill_id[fill.broker_fill_id] = fill
        if any(not fill_id.isascii() or not fill_id.isdigit() for fill_id in by_fill_id):
            raise ProviderSchemaError("Provider fill identity is not a decimal ID")
        ordered_ids = tuple(sorted(by_fill_id, key=int))
        ordered_fills = tuple(by_fill_id[fill_id] for fill_id in ordered_ids)
        # Parsed quantities are bounded by the instrument step; precision 80 safely
        # covers their 38-digit coefficients and the maximum supported row count.
        with localcontext(Context(prec=80)):
            filled_quantity = sum((fill.quantity for fill in ordered_fills), Decimal(0))
        if filled_quantity != first_order.filled_quantity:
            raise ProviderSchemaError("Provider history fill sum differs from order filledQuantity")
        aggregate_id = _provider_order_fill_aggregate_hash(
            first_order.broker_order_id,
            expected_local_order.order_id,
            first_order,
            ordered_fills,
            filled_quantity,
        )
        aggregate = object.__new__(cls)
        object.__setattr__(aggregate, "broker_order_id", first_order.broker_order_id)
        object.__setattr__(aggregate, "local_order_id", expected_local_order.order_id)
        object.__setattr__(aggregate, "mapped_order", first_order)
        object.__setattr__(aggregate, "ticker", first_order.ticker)
        object.__setattr__(aggregate, "instrument", first_order.instrument)
        object.__setattr__(aggregate, "side", first_order.side)
        object.__setattr__(aggregate, "status", first_order.status.provider)
        object.__setattr__(aggregate, "order_quantity", first_order.quantity)
        object.__setattr__(aggregate, "fills", ordered_fills)
        object.__setattr__(aggregate, "fill_ids", ordered_ids)
        object.__setattr__(aggregate, "filled_quantity", filled_quantity)
        object.__setattr__(aggregate, "aggregate_id", aggregate_id)
        return aggregate


def _provider_order_fill_aggregate_hash(
    broker_order_id: str,
    local_order_id: OrderId,
    order: MappedProviderOrder,
    fills: tuple[ProviderFillRecord, ...],
    filled_quantity: Decimal,
) -> str:
    data = {
        "broker_order_id": broker_order_id,
        "local_order_id": local_order_id.value,
        "mapped_order_id": order.order_id.value,
        "ticker": order.ticker,
        "instrument_id": order.instrument.instrument_id.value,
        "instrument_currency": order.instrument.currency,
        "side": order.side.value,
        "status": order.status.provider.value,
        "order_quantity": str(order.quantity),
        "order_filled_quantity": str(order.filled_quantity),
        "limit_price": str(order.limit_price),
        "order_created_at": order.created_at.astimezone(UTC).isoformat(),
        "filled_quantity": str(filled_quantity),
        "fills": [
            {
                "fill_id": fill.broker_fill_id,
                "order_id": fill.broker_order_id,
                "ticker": fill.ticker,
                "side": fill.side.value,
                "quantity": str(fill.quantity),
                "price": str(fill.price),
                "timestamp": fill.timestamp.astimezone(UTC).isoformat(),
            }
            for fill in fills
        ],
    }
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BrokerAccountSummary:
    account_id: int
    currency: str
    available_to_trade: Decimal
    cash_in_pies: Decimal
    reserved_for_orders: Decimal
    investment_current_value: Decimal
    investment_realized_profit_loss: Decimal
    investment_total_cost: Decimal
    investment_unrealized_profit_loss: Decimal
    total_value: Decimal


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    ticker: str
    instrument: InstrumentMeta
    instrument_currency: str
    quantity: Decimal
    quantity_available: Decimal
    quantity_in_pies: Decimal
    average_price_paid: Decimal
    current_price: Decimal
    created_at: datetime
    account_currency: str
    current_value: Decimal
    fx_impact: Decimal | None
    total_cost: Decimal
    unrealized_profit_loss: Decimal
    isin: str
    name: str


@dataclass(frozen=True, slots=True)
class BrokerPortfolioSnapshot:
    positions: tuple[BrokerPosition, ...]


@dataclass(frozen=True, slots=True)
class PositionReconciliation:
    ticker: str
    local_quantity: Decimal
    broker_quantity: Decimal
    matches: bool


@dataclass(frozen=True, slots=True)
class BrokerReconciliation:
    currency: str
    broker_snapshot_id: str
    local_available_cash: Decimal
    broker_available_cash: Decimal
    cash_matches: bool
    positions: tuple[PositionReconciliation, ...]
    matches: bool


def compare_local_broker_state(
    account: BrokerAccountSummary,
    portfolio: BrokerPortfolioSnapshot,
    *,
    local_currency: str,
    local_available_cash: Decimal,
    local_positions: Mapping[str, Decimal],
) -> BrokerReconciliation:
    """Compare exact local available cash and quantities without mutating inputs."""
    if not isinstance(account, BrokerAccountSummary) or not isinstance(
        portfolio, BrokerPortfolioSnapshot
    ):
        raise DomainError("Parsed broker account and portfolio snapshots are required")
    if local_currency != account.currency:
        raise ProviderSchemaError("Local and broker account currencies differ")
    if not re.fullmatch(r"[A-Z]{3}", account.currency):
        raise ProviderSchemaError("Broker account snapshot currency is malformed")
    if (
        not isinstance(account.available_to_trade, Decimal)
        or not account.available_to_trade.is_finite()
        or account.available_to_trade < 0
    ):
        raise ProviderSchemaError("Broker available cash is malformed")
    if not isinstance(local_available_cash, Decimal) or not local_available_cash.is_finite():
        raise DomainError("Local available cash must be an exact finite Decimal")
    if local_available_cash < 0:
        raise DomainError("Local available cash cannot be negative")
    if not isinstance(local_positions, Mapping):
        raise DomainError("Local positions must be a ticker-to-Decimal mapping")
    normalized_local: dict[str, Decimal] = {}
    for ticker, quantity in local_positions.items():
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9_]+", ticker):
            raise DomainError("Local position ticker is malformed")
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity < 0:
            raise DomainError("Local position quantities must be nonnegative finite Decimals")
        normalized_local[ticker] = quantity
    broker_quantities: dict[str, Decimal] = {}
    for position in portfolio.positions:
        if not isinstance(position, BrokerPosition):
            raise ProviderSchemaError("Portfolio snapshot contains an unparsed position")
        if position.account_currency != account.currency:
            raise ProviderSchemaError("Portfolio position currency differs from account snapshot")
        if (
            not isinstance(position.ticker, str)
            or not re.fullmatch(r"[A-Z0-9_]+", position.ticker)
            or not isinstance(position.instrument, InstrumentMeta)
            or position.instrument.currency != position.instrument_currency
            or not isinstance(position.quantity, Decimal)
            or not position.quantity.is_finite()
            or position.quantity <= 0
        ):
            raise ProviderSchemaError("Portfolio snapshot contains malformed position data")
        if position.ticker in broker_quantities:
            raise ProviderSchemaError("Portfolio snapshot contains duplicate positions")
        broker_quantities[position.ticker] = position.quantity
    position_rows = tuple(
        PositionReconciliation(
            ticker=ticker,
            local_quantity=normalized_local.get(ticker, Decimal(0)),
            broker_quantity=broker_quantities.get(ticker, Decimal(0)),
            matches=normalized_local.get(ticker, Decimal(0))
            == broker_quantities.get(ticker, Decimal(0)),
        )
        for ticker in sorted(set(normalized_local) | set(broker_quantities))
    )
    cash_matches = local_available_cash == account.available_to_trade
    all_match = cash_matches and all(row.matches for row in position_rows)
    snapshot_content = {
        "account": {
            "account_id": account.account_id,
            "currency": account.currency,
            "available_to_trade": str(account.available_to_trade),
            "cash_in_pies": str(account.cash_in_pies),
            "reserved_for_orders": str(account.reserved_for_orders),
            "investment_current_value": str(account.investment_current_value),
            "investment_realized_profit_loss": str(account.investment_realized_profit_loss),
            "investment_total_cost": str(account.investment_total_cost),
            "investment_unrealized_profit_loss": str(account.investment_unrealized_profit_loss),
            "total_value": str(account.total_value),
        },
        "positions": [
            {
                "ticker": position.ticker,
                "instrument_id": position.instrument.instrument_id.value,
                "instrument_currency": position.instrument_currency,
                "quantity": str(position.quantity),
                "quantity_available": str(position.quantity_available),
                "quantity_in_pies": str(position.quantity_in_pies),
                "average_price_paid": str(position.average_price_paid),
                "current_price": str(position.current_price),
                "created_at": position.created_at.astimezone(UTC).isoformat(),
                "account_currency": position.account_currency,
                "current_value": str(position.current_value),
                "fx_impact": None if position.fx_impact is None else str(position.fx_impact),
                "total_cost": str(position.total_cost),
                "unrealized_profit_loss": str(position.unrealized_profit_loss),
                "isin": position.isin,
                "name": position.name,
            }
            for position in sorted(portfolio.positions, key=lambda item: item.ticker)
        ],
    }
    canonical = json.dumps(snapshot_content, sort_keys=True, separators=(",", ":"))
    broker_snapshot_id = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return BrokerReconciliation(
        currency=account.currency,
        broker_snapshot_id=broker_snapshot_id,
        local_available_cash=local_available_cash,
        broker_available_cash=account.available_to_trade,
        cash_matches=cash_matches,
        positions=position_rows,
        matches=all_match,
    )


def _decimal(
    value: object, field: str, *, positive: bool = False, allow_negative: bool = False
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ProviderSchemaError(f"Provider {field} has an invalid numeric type")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProviderSchemaError(f"Provider {field} is malformed") from exc
    if not result.is_finite() or (result < 0 and not allow_negative) or (positive and result == 0):
        raise ProviderSchemaError(f"Provider {field} is outside the supported range")
    return result


def _aware_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ProviderSchemaError(f"Provider {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderSchemaError(f"Provider {field} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderSchemaError(f"Provider {field} must include a timezone")
    return parsed.astimezone(UTC)


def _required_string(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ProviderSchemaError(f"Provider field {key} is missing or malformed")
    return value


def _require_object_fields(
    row: object, expected: frozenset[str], label: str
) -> Mapping[str, object]:
    if not isinstance(row, Mapping):
        raise ProviderSchemaError(f"Provider {label} must be an object")
    missing = expected - set(row)
    extra = set(row) - expected
    if missing or extra:
        raise ProviderSchemaError(
            f"Provider {label} fields mismatch (missing={sorted(missing)}, extra={sorted(extra)})"
        )
    return row


def _multiple(value: Decimal, step: Decimal) -> bool:
    value_tuple, step_tuple = value.as_tuple(), step.as_tuple()
    value_exponent, step_exponent = value_tuple.exponent, step_tuple.exponent
    if not isinstance(value_exponent, int) or not isinstance(step_exponent, int):
        return False
    # Bound scale and coefficient length before integer conversion or exponentiation.
    # Provider numeric strings are untrusted and may encode extreme finite exponents.
    if (
        len(value_tuple.digits) > 38
        or value_exponent < -18
        or value_exponent > 18
        or len(value_tuple.digits) + max(value_exponent, 0) > 38
        or len(step_tuple.digits) > 38
        or step_exponent < -18
        or step_exponent > 18
        or len(step_tuple.digits) + max(step_exponent, 0) > 38
    ):
        return False
    exponent = min(value_exponent, step_exponent)
    value_units = int("".join(map(str, value_tuple.digits)) or "0") * 10 ** (
        value_exponent - exponent
    )
    step_units = int("".join(map(str, step_tuple.digits)) or "0") * 10 ** (step_exponent - exponent)
    return bool(step_units > 0 and value_units % step_units == 0)


class Trading212Adapter:
    """Build reviewed Demo requests and strictly parse supplied provider fixtures."""

    base_url = DEMO_BASE_URL
    api_version = API_VERSION

    def __init__(
        self,
        credentials: DemoCredentials,
        instruments: Mapping[str, InstrumentMeta],
        account_currency: str,
    ) -> None:
        if not isinstance(credentials, DemoCredentials):
            raise DomainError("Ephemeral DemoCredentials are required")
        if not isinstance(account_currency, str) or not re.fullmatch(r"[A-Z]{3}", account_currency):
            raise DomainError("account_currency must be an ISO-style uppercase code")
        if not instruments or any(
            not isinstance(ticker, str)
            or not re.fullmatch(r"[A-Z0-9_]+", ticker)
            or not isinstance(meta, InstrumentMeta)
            for ticker, meta in instruments.items()
        ):
            raise DomainError("A trusted provider ticker to InstrumentMeta registry is required")
        self._credentials = credentials
        self._instruments = dict(instruments)
        self._account_currency = account_currency

    def _request(
        self, method: str, path: str, body: Mapping[str, object] | None = None
    ) -> RequestDescription:
        if self.base_url != DEMO_BASE_URL:
            raise DomainError("Only the fixed Trading 212 Demo host is supported")
        if not path.startswith("/equity/") or ".." in path or "\r" in path or "\n" in path:
            raise DomainError("Only fixed Trading 212 Demo API paths are supported")
        headers: tuple[tuple[str, str], ...] = (
            ("Authorization", self._credentials.basic_authorization()),
            ("Accept", "application/json"),
            ("X-Client-Api-Version", API_VERSION),
        )
        if body is not None:
            headers += (("Content-Type", "application/json"),)
        return RequestDescription(
            method=method,
            url=self.base_url + path,
            headers=headers,
            body=tuple(body.items()) if body is not None else None,
        )

    def describe_account_summary_request(self) -> RequestDescription:
        return self._request("GET", "/equity/account/summary")

    def describe_positions_request(self) -> RequestDescription:
        return self._request("GET", "/equity/positions")

    def aggregate_historical_order_fills(
        self,
        history_rows: Iterable[object],
        *,
        expected_broker_order_id: str,
        expected_local_order: OrderIntent,
    ) -> ProviderOrderFillAggregate:
        """Parse provider history and bind exact fills to one local order intent."""
        parsed_rows: list[tuple[MappedProviderOrder, ProviderFillRecord]] = []
        for row in history_rows:
            if len(parsed_rows) >= 10000:
                raise ProviderSchemaError("History aggregate exceeds the 10,000-fill safety bound")
            parsed_rows.append(self.parse_historical_order(row))
        return ProviderOrderFillAggregate.from_parsed_history(
            parsed_rows,
            expected_broker_order_id=expected_broker_order_id,
            expected_local_order=expected_local_order,
        )

    def describe_pending_orders_request(self) -> RequestDescription:
        return self._request("GET", "/equity/orders")

    def describe_order_history_request(self) -> RequestDescription:
        return self._request("GET", "/equity/history/orders?limit=50")

    def parse_account_summary(self, payload: object) -> BrokerAccountSummary:
        """Parse `/equity/account/summary` with exact, reviewed object fields."""
        row = _require_object_fields(payload, _ACCOUNT_FIELDS, "account summary")
        account_id = row["id"]
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise ProviderSchemaError("Provider account ID is missing or invalid")
        currency = _required_string(row, "currency")
        if not re.fullmatch(r"[A-Z]{3}", currency) or currency != self._account_currency:
            raise ProviderSchemaError("Provider account currency differs from configured currency")
        cash = _require_object_fields(row["cash"], _CASH_FIELDS, "cash summary")
        investments = _require_object_fields(
            row["investments"], _INVESTMENT_FIELDS, "investment summary"
        )
        return BrokerAccountSummary(
            account_id=account_id,
            currency=currency,
            available_to_trade=_decimal(cash["availableToTrade"], "availableToTrade"),
            cash_in_pies=_decimal(cash["inPies"], "inPies"),
            reserved_for_orders=_decimal(cash["reservedForOrders"], "reservedForOrders"),
            investment_current_value=_decimal(
                investments["currentValue"], "investment currentValue"
            ),
            investment_realized_profit_loss=_decimal(
                investments["realizedProfitLoss"],
                "investment realizedProfitLoss",
                allow_negative=True,
            ),
            investment_total_cost=_decimal(investments["totalCost"], "investment totalCost"),
            investment_unrealized_profit_loss=_decimal(
                investments["unrealizedProfitLoss"],
                "investment unrealizedProfitLoss",
                allow_negative=True,
            ),
            total_value=_decimal(row["totalValue"], "totalValue"),
        )

    def parse_portfolio(self, payload: object) -> BrokerPortfolioSnapshot:
        """Parse `/equity/positions`; every row must match trusted metadata."""
        if not isinstance(payload, list):
            raise ProviderSchemaError("Provider portfolio must be an array")
        positions: list[BrokerPosition] = []
        seen_tickers: set[str] = set()
        for index, raw_position in enumerate(payload):
            row = _require_object_fields(raw_position, _POSITION_FIELDS, f"position[{index}]")
            raw_instrument = _require_object_fields(
                row["instrument"], _POSITION_INSTRUMENT_FIELDS, f"position[{index}].instrument"
            )
            ticker = _required_string(raw_instrument, "ticker")
            instrument = self._instruments.get(ticker)
            if instrument is None:
                raise ProviderSchemaError("Provider position ticker is not in trusted registry")
            instrument_currency = _required_string(raw_instrument, "currency")
            if (
                not re.fullmatch(r"[A-Z]{3}", instrument_currency)
                or instrument_currency != instrument.currency
            ):
                raise ProviderSchemaError("Provider position instrument currency is untrusted")
            if ticker in seen_tickers:
                raise ProviderSchemaError("Provider portfolio contains a duplicate ticker")
            seen_tickers.add(ticker)
            name = _required_string(raw_instrument, "name")
            isin = _required_string(raw_instrument, "isin")
            if not re.fullmatch(r"[A-Z0-9]{12}", isin):
                raise ProviderSchemaError("Provider position ISIN is malformed")

            quantity = _decimal(row["quantity"], "position quantity", positive=True)
            available = _decimal(row["quantityAvailableForTrading"], "quantityAvailableForTrading")
            in_pies = _decimal(row["quantityInPies"], "quantityInPies")
            if (
                available > quantity
                or in_pies > quantity
                or available + in_pies > quantity
                or not _multiple(quantity, instrument.quantity_step)
                or not _multiple(available, instrument.quantity_step)
                or not _multiple(in_pies, instrument.quantity_step)
            ):
                raise ProviderSchemaError("Provider position quantities violate bounds or step")
            current_price = _decimal(row["currentPrice"], "currentPrice", positive=True)
            average_price = _decimal(row["averagePricePaid"], "averagePricePaid", positive=True)
            if not _multiple(current_price, instrument.tick_size):
                raise ProviderSchemaError("Provider current price violates trusted tick size")
            raw_wallet = _require_object_fields(
                row["walletImpact"], _POSITION_WALLET_FIELDS, f"position[{index}].walletImpact"
            )
            wallet_currency = _required_string(raw_wallet, "currency")
            if wallet_currency != self._account_currency:
                raise ProviderSchemaError("Provider position wallet currency differs from account")
            fx_raw = raw_wallet["fxImpact"]
            fx_impact = (
                None if fx_raw is None else _decimal(fx_raw, "fxImpact", allow_negative=True)
            )
            positions.append(
                BrokerPosition(
                    ticker=ticker,
                    instrument=instrument,
                    instrument_currency=instrument_currency,
                    quantity=quantity,
                    quantity_available=available,
                    quantity_in_pies=in_pies,
                    average_price_paid=average_price,
                    current_price=current_price,
                    created_at=_aware_timestamp(row["createdAt"], "position createdAt"),
                    account_currency=wallet_currency,
                    current_value=_decimal(raw_wallet["currentValue"], "wallet currentValue"),
                    fx_impact=fx_impact,
                    total_cost=_decimal(raw_wallet["totalCost"], "wallet totalCost"),
                    unrealized_profit_loss=_decimal(
                        raw_wallet["unrealizedProfitLoss"],
                        "wallet unrealizedProfitLoss",
                        allow_negative=True,
                    ),
                    isin=isin,
                    name=name,
                )
            )
        return BrokerPortfolioSnapshot(tuple(positions))

    def describe_limit_order_request(self, intent: OrderIntent) -> RequestDescription:
        """Map one supported LIMIT intent to Demo wire fields; never transmits it."""
        if not isinstance(intent, OrderIntent):
            raise DomainError("A validated OrderIntent is required")
        if intent.order_type is not TradeType.LIMIT or intent.limit_price is None:
            raise DomainError("Only LIMIT orders are mapped by this adapter")
        if intent.instrument.currency != self._account_currency:
            raise DomainError("Trading 212 order currency must match primary account currency")
        ticker = self._ticker_for(intent.instrument)
        signed_quantity = intent.quantity if intent.side is Side.BUY else -intent.quantity
        # Decimal strings avoid binary-float conversion and preserve exact inputs.
        payload = {
            "ticker": ticker,
            "quantity": str(signed_quantity),
            "limitPrice": str(intent.limit_price),
            "timeValidity": "DAY",
        }
        return self._request("POST", "/equity/orders/limit", payload)

    def _ticker_for(self, instrument: InstrumentMeta) -> str:
        matches = [ticker for ticker, known in self._instruments.items() if known == instrument]
        if len(matches) != 1:
            raise DomainError(
                "Order instrument is not uniquely present in trusted provider registry"
            )
        return matches[0]

    def parse_order(self, row: object) -> MappedProviderOrder:
        """Parse a provider order row without accepting unknown status or currency."""
        if not isinstance(row, Mapping):
            raise ProviderSchemaError("Provider order must be an object")
        if set(row) - _ORDER_FIELDS:
            raise ProviderSchemaError("Provider order contains fields outside the reviewed schema")
        raw_id = row.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
            raise ProviderSchemaError("Provider order ID is missing or invalid")
        ticker = _required_string(row, "ticker")
        instrument = self._instruments.get(ticker)
        if instrument is None:
            raise ProviderSchemaError("Provider order ticker is not in the trusted registry")
        currency = _required_string(row, "currency")
        if currency != self._account_currency or instrument.currency != self._account_currency:
            raise ProviderSchemaError(
                "Provider order currency differs from configured account currency"
            )
        side_raw = _required_string(row, "side")
        try:
            side = Side(side_raw.lower())
        except ValueError as exc:
            raise ProviderSchemaError("Provider order side is unknown") from exc
        status_raw = _required_string(row, "status")
        try:
            provider_status = ProviderOrderStatus(status_raw)
        except ValueError:
            provider_status = ProviderOrderStatus.UNKNOWN
        status = _STATUS_MAP.get(provider_status)
        if status is None:
            raise ProviderSchemaError(
                "Provider order status is unsupported; reconciliation is blocked"
            )
        order_type = _required_string(row, "type")
        if order_type != "LIMIT":
            raise ProviderSchemaError("Only provider LIMIT orders are supported by this adapter")
        signed_quantity = _decimal(row.get("quantity"), "quantity", allow_negative=True)
        signed_filled = _decimal(row.get("filledQuantity"), "filledQuantity", allow_negative=True)
        expected_sign = Decimal(1) if side is Side.BUY else Decimal(-1)
        if signed_quantity * expected_sign <= 0:
            raise ProviderSchemaError("Provider quantity sign conflicts with BUY/SELL side")
        if signed_filled and signed_filled * expected_sign <= 0:
            raise ProviderSchemaError("Provider filled quantity sign conflicts with BUY/SELL side")
        quantity = abs(signed_quantity)
        filled = abs(signed_filled)
        if not _multiple(quantity, instrument.quantity_step) or not _multiple(
            filled, instrument.quantity_step
        ):
            raise ProviderSchemaError("Provider order quantities violate trusted quantity step")
        limit_price = _decimal(row.get("limitPrice"), "limitPrice", positive=True)
        if not _multiple(limit_price, instrument.tick_size):
            raise ProviderSchemaError("Provider limitPrice violates trusted tick size")
        if filled > quantity:
            raise ProviderSchemaError("Provider filled quantity exceeds order quantity")
        if provider_status is ProviderOrderStatus.FILLED and filled != quantity:
            raise ProviderSchemaError("FILLED order quantity disagrees with filledQuantity")
        if provider_status is ProviderOrderStatus.PARTIALLY_FILLED and not (0 < filled < quantity):
            raise ProviderSchemaError("PARTIALLY_FILLED order has inconsistent filledQuantity")
        return MappedProviderOrder(
            broker_order_id=str(raw_id),
            order_id=OrderId(f"t212:{raw_id}"),
            ticker=ticker,
            instrument=instrument,
            side=side,
            status=status,
            quantity=quantity,
            filled_quantity=filled,
            limit_price=limit_price,
            created_at=_aware_timestamp(row.get("createdAt"), "createdAt"),
        )

    def parse_historical_order(self, row: object) -> tuple[MappedProviderOrder, ProviderFillRecord]:
        """Parse one history item; require provider fill identity and facts."""
        row = _require_object_fields(row, frozenset({"order", "fill"}), "historical order envelope")
        order = self.parse_order(row.get("order"))
        raw_fill = row.get("fill")
        if not isinstance(raw_fill, Mapping):
            raise ProviderSchemaError("Historical record has no provider fill; no fill is inferred")
        if set(raw_fill) - _FILL_FIELDS:
            raise ProviderSchemaError("Provider fill contains fields outside the reviewed schema")
        fill_id = raw_fill.get("id")
        if isinstance(fill_id, bool) or not isinstance(fill_id, int) or fill_id <= 0:
            raise ProviderSchemaError("Provider fill identity is missing or invalid")
        if _required_string(raw_fill, "type") != "TRADE":
            raise ProviderSchemaError("Non-trade corporate-action fills require a separate mapper")
        quantity_raw = _decimal(raw_fill.get("quantity"), "fill quantity", allow_negative=True)
        expected_sign = Decimal(1) if order.side is Side.BUY else Decimal(-1)
        if quantity_raw * expected_sign <= 0:
            raise ProviderSchemaError("Provider fill quantity sign conflicts with order side")
        if abs(quantity_raw) > order.quantity:
            raise ProviderSchemaError("Provider fill quantity exceeds parent order quantity")
        if order.filled_quantity <= 0:
            raise ProviderSchemaError("Provider fill conflicts with zero aggregate filledQuantity")
        if abs(quantity_raw) > order.filled_quantity:
            raise ProviderSchemaError("Provider fill exceeds order's aggregate filledQuantity")
        if order.status.internal not in (
            OrderStatus.FILLED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCELLED,
        ):
            raise ProviderSchemaError("Provider fill conflicts with non-executed order status")
        fill_quantity = abs(quantity_raw)
        if not _multiple(fill_quantity, order.instrument.quantity_step):
            raise ProviderSchemaError("Provider fill quantity violates trusted quantity step")
        fill_price = _decimal(raw_fill.get("price"), "fill price", positive=True)
        if not _multiple(fill_price, order.instrument.tick_size):
            raise ProviderSchemaError("Provider fill price violates trusted tick size")
        fill = ProviderFillRecord(
            broker_fill_id=str(fill_id),
            broker_order_id=order.broker_order_id,
            ticker=order.ticker,
            side=order.side,
            quantity=fill_quantity,
            price=fill_price,
            timestamp=_aware_timestamp(raw_fill.get("filledAt"), "filledAt"),
        )
        return order, fill


def parse_rate_limit_headers(headers: Mapping[str, str]) -> dict[str, int | float]:
    """Validate Trading 212 x-ratelimit headers; callers must block on bad data."""
    normalized = {key.lower(): value for key, value in headers.items()}
    keys = {
        "limit": "x-ratelimit-limit",
        "period": "x-ratelimit-period",
        "remaining": "x-ratelimit-remaining",
        "reset": "x-ratelimit-reset",
        "used": "x-ratelimit-used",
    }
    try:
        values = {name: normalized[header] for name, header in keys.items()}
        limit = int(values["limit"])
        period = float(values["period"])
        remaining = int(values["remaining"])
        reset = float(values["reset"])
        used = int(values["used"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderSchemaError(
            "Rate-limit headers missing or malformed; endpoint is blocked"
        ) from exc
    if (
        limit <= 0
        or period <= 0
        or not (0 <= remaining <= limit)
        or used < 0
        or used + remaining > limit
        or reset <= 0
        or not math.isfinite(period)
        or not math.isfinite(reset)
    ):
        raise ProviderSchemaError("Rate-limit headers are inconsistent; endpoint is blocked")
    return {
        "limit": limit,
        "period_seconds": period,
        "remaining": remaining,
        "reset_epoch": reset,
        "used": used,
    }


def parse_retry_after(value: str | None, *, now: datetime) -> datetime | None:
    """Parse Retry-After seconds or HTTP date without silently retrying malformed values."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderSchemaError("Retry-After header is malformed")
    raw = value.strip()
    if raw.isdigit():
        seconds = int(raw)
        if seconds > 86400:
            raise ProviderSchemaError("Retry-After exceeds the one-day safety bound")
        if now.tzinfo is None or now.utcoffset() is None:
            raise DomainError("now must be timezone-aware")
        return now.astimezone(UTC) + timedelta(seconds=seconds)
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderSchemaError("Retry-After header is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderSchemaError("Retry-After date must include a timezone")
    return parsed.astimezone(UTC)
