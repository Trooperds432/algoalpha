"""Trusted bridge from as-of market observations to isolated order proposals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, Context, Decimal, localcontext
from typing import Protocol

from quant_terminal.backtesting.strategies import StrategyContext
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId, OrderId
from quant_terminal.data.model import Bar
from quant_terminal.orders.models import OrderIntent, Side, TradeType
from quant_terminal.sandbox.protocol import (
    MAX_DATA_ROWS,
    VERSION,
    SandboxBudget,
    SandboxRequest,
    SandboxResponse,
    decode_response,
    encode_request,
    encode_response,
)


class StrategyBackend(Protocol):
    def execute(self, request_blob: bytes) -> SandboxResponse: ...

    def identity(self) -> str: ...


def _row(bar: Bar) -> dict[str, str]:
    # Explicit allowlist: never serialize a context, feed, store or portfolio
    # object, any of which would confer host authority or future-data access.
    return {
        "instrument_id": bar.instrument_id.value,
        "start": bar.start.isoformat(),
        "end": bar.end.isoformat(),
        "available_at": bar.available_at.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }


@dataclass(frozen=True, slots=True)
class IsolatedStrategy:
    """One fresh sandbox per observation, behind the normal engine risk path.

    This internal bridge does not enable arbitrary source admission in the
    public terminal. The operator must supply a verified backend explicitly.
    """

    backend: StrategyBackend
    source: str
    instrument_ids: tuple[InstrumentId, ...]
    budget: SandboxBudget
    seed: int = 0
    history_count: int = 256
    name: str = "isolated_python"
    _data_hashes: list[str] = field(default_factory=list, init=False, repr=False, compare=False)
    _backend_identities: list[str] = field(
        default_factory=list, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if (
            not self.instrument_ids
            or len(set(self.instrument_ids)) != len(self.instrument_ids)
            or any(not isinstance(identity, InstrumentId) for identity in self.instrument_ids)
        ):
            raise DomainError("Isolated strategy needs distinct configured instruments")
        if type(self.history_count) is not int or not 1 <= self.history_count <= MAX_DATA_ROWS:
            raise DomainError("Isolated history count exceeds the protocol budget")

    def begin_run(self) -> None:
        self._data_hashes.clear()
        self._backend_identities.clear()

    def run_metadata(self) -> tuple[tuple[str, str], ...]:
        return (
            ("isolated_source_sha256", hashlib.sha256(self.source.encode("utf-8")).hexdigest()),
            (
                "isolated_data_slices_sha256",
                hashlib.sha256("\n".join(self._data_hashes).encode("ascii")).hexdigest(),
            ),
            ("isolated_data_slice_count", str(len(self._data_hashes))),
            ("isolated_dependencies_sha256", hashlib.sha256(b"{}").hexdigest()),
            (
                "isolated_budget",
                json.dumps(asdict(self.budget), sort_keys=True, separators=(",", ":")),
            ),
            ("isolated_seed", str(self.seed)),
            ("isolated_protocol_version", str(VERSION)),
            (
                "isolated_backend_identity",
                self._backend_identities[0] if self._backend_identities else "not_executed",
            ),
        )

    def on_bar(self, context: StrategyContext, bar: Bar) -> tuple[OrderIntent, ...]:
        if bar.instrument_id not in self.instrument_ids:
            raise DomainError("Isolated strategy received an unconfigured observation")
        if bar.available_at > context.current_time or bar.end > context.current_time:
            raise DomainError("Isolated strategy received a future observation")
        visible = [
            item
            for identity in self.instrument_ids
            for item in context.history(identity, self.history_count, end=context.current_time)
            if item != bar
        ]
        if any(item.available_at > context.current_time for item in visible):
            raise DomainError("Future history cannot cross the sandbox boundary")
        visible.sort(key=lambda item: (item.available_at, item.instrument_id.value, item.start))
        rows = [_row(item) for item in visible[-(MAX_DATA_ROWS - 1) :]] + [_row(bar)]
        rows[-1]["position"] = str(context.position(bar.instrument_id))
        rows[-1]["has_active_order"] = str(context.has_active_order(bar.instrument_id))
        rows[-1]["as_of"] = context.current_time.isoformat()
        data_blob = json.dumps(
            rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        source_hash = hashlib.sha256(self.source.encode("utf-8")).hexdigest()
        data_hash = hashlib.sha256(data_blob).hexdigest()
        identity = f"iso-{source_hash[:16]}-{data_hash[:16]}"
        request = SandboxRequest(
            identity,
            self.source,
            source_hash,
            tuple(tuple(sorted(row.items())) for row in rows),
            data_hash,
            (),
            self.seed,
            self.budget,
        )
        response = decode_response(encode_response(self.backend.execute(encode_request(request))))
        backend_identity = self.backend.identity()
        if self._backend_identities and self._backend_identities[0] != backend_identity:
            raise DomainError("Isolated backend changed during the run")
        if not self._backend_identities:
            self._backend_identities.append(backend_identity)
        self._data_hashes.append(data_hash)
        if response.job_id != identity or response.status != "ok":
            raise DomainError("Isolated strategy failed to return valid proposals")
        proposals: list[OrderIntent] = []
        for index, intent in enumerate(response.intents):
            instrument_id = InstrumentId(intent.instrument_id)
            if instrument_id not in self.instrument_ids:
                raise DomainError("Isolated strategy proposed an unconfigured instrument")
            if instrument_id != bar.instrument_id:
                raise DomainError(
                    "Cross-instrument proposals require a separately priced observation"
                )
            instrument = context.instrument(instrument_id)
            cap = None
            if intent.side == "buy" and intent.order_type in {"market", "stop"}:
                # Same protective reservation policy as the built-in strategies;
                # untrusted code cannot omit the cash-constrained execution cap.
                with localcontext(Context(prec=34)):
                    units = (bar.close * Decimal("1.25") / instrument.tick_size).to_integral_value(
                        rounding=ROUND_CEILING
                    )
                    cap = units * instrument.tick_size
            proposals.append(
                OrderIntent(
                    OrderId(f"{identity}-{index}"),
                    instrument,
                    Side(intent.side),
                    TradeType(intent.order_type),
                    Decimal(intent.quantity),
                    context.current_time,
                    limit_price=None if intent.limit_price is None else Decimal(intent.limit_price),
                    stop_price=None if intent.stop_price is None else Decimal(intent.stop_price),
                    max_execution_price=cap,
                )
            )
        # No ledger or broker reference crosses the boundary. These proposals
        # still pass BacktestEngine's ordinary risk and execution checks.
        return tuple(proposals)
