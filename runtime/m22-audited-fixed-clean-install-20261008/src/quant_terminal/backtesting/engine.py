"""Point-in-time event loop joining data, strategies, execution and accounting."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from types import MappingProxyType

from quant_terminal.accounting.ledger import PortfolioLedger, PortfolioSnapshot
from quant_terminal.core.clock import SimulationClock, utc
from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import FillId, InstrumentId, OrderId
from quant_terminal.data.feed import DataFeed
from quant_terminal.data.model import Bar, InstrumentMeta
from quant_terminal.data.storage import DatasetSnapshot, DatasetStore
from quant_terminal.execution.models import ExecutionConfig
from quant_terminal.execution.simulator import HistoricalBroker, _floor_step
from quant_terminal.orders.models import (
    Fill,
    OrderIntent,
    OrderSnapshot,
    Side,
    TradeType,
    to_ledger_fill,
)

from .models import BacktestConfig, BacktestResult, BenchmarkPoint, NAVPoint
from .strategies import Strategy, StrategyContext, StrategyRunMetadata


def _context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


def _is_builtin_zero_cost(execution: ExecutionConfig) -> bool:
    commission = execution.commission
    slippage = execution.slippage
    return (
        getattr(commission, "per_unit_rate", None) == 0
        and getattr(commission, "minimum", None) in (None, Decimal(0))
        and getattr(getattr(slippage, "kind", None), "value", None) == "none"
        and getattr(slippage, "value", None) == 0
    )


def _declares_zero_cost(execution: ExecutionConfig) -> bool:
    return any(
        getattr(model, "is_zero_cost", False) is True
        for model in (execution.commission, execution.slippage)
    )


def _nav_point(
    timestamp: datetime, portfolio: PortfolioSnapshot, period_return: Decimal | None
) -> NAVPoint:
    return NAVPoint(
        timestamp=timestamp,
        nav=portfolio.nav,
        period_return=period_return,
        cash=portfolio.cash,
        positions=portfolio.positions,
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=portfolio.unrealized_pnl,
        long_exposure=portfolio.long_exposure,
        short_exposure=portfolio.short_exposure,
        gross_exposure=portfolio.gross_exposure,
        net_exposure=portfolio.net_exposure,
        leverage=portfolio.leverage,
        reconciliation_error=portfolio.reconciliation_error,
    )


class BacktestEngine:
    """Run a trusted strategy over immutable, point-in-time data snapshots."""

    def __init__(
        self,
        store: DatasetStore,
        snapshots: Iterable[DatasetSnapshot],
        strategy: Strategy,
        config: BacktestConfig,
        execution: ExecutionConfig | None = None,
    ) -> None:
        if not isinstance(store, DatasetStore):
            raise DomainError("DatasetStore is required")
        raw_snapshots = tuple(snapshots)
        if any(not isinstance(item, DatasetSnapshot) for item in raw_snapshots):
            raise DomainError("Only DatasetSnapshot values are supported")
        snapshot_values = tuple(
            sorted(raw_snapshots, key=lambda item: item.instrument.instrument_id.value)
        )
        if not snapshot_values:
            raise DomainError("At least one DatasetSnapshot is required")
        if not isinstance(config, BacktestConfig):
            raise DomainError("BacktestConfig is required")
        if not callable(getattr(strategy, "on_bar", None)):
            raise DomainError("Strategy must implement on_bar")
        if not isinstance(getattr(strategy, "name", None), str) or not strategy.name.strip():
            raise DomainError("Strategy must have a nonempty name")
        if execution is None:
            execution = ExecutionConfig(
                zero_cost_assumption="ZERO_COST_ASSUMPTION" if config.allow_zero_costs else None
            )
        if not isinstance(execution, ExecutionConfig):
            raise DomainError("ExecutionConfig is required")
        if execution.zero_cost_assumption is not None and not config.allow_zero_costs:
            raise DomainError("Zero-cost execution requires allow_zero_costs=True")
        if (
            _is_builtin_zero_cost(execution) or _declares_zero_cost(execution)
        ) and not config.allow_zero_costs:
            raise DomainError("Zero-cost execution requires allow_zero_costs=True")
        if config.allow_zero_costs and (
            _is_builtin_zero_cost(execution) or _declares_zero_cost(execution)
        ):
            if execution.zero_cost_assumption != "ZERO_COST_ASSUMPTION":
                raise DomainError("Zero-cost execution requires ZERO_COST_ASSUMPTION label")
        self.store = store
        self.snapshots = snapshot_values
        self.strategy = strategy
        self.config = config
        self.execution = execution

    def run(
        self,
        *,
        start: datetime,
        end: datetime,
        benchmark: InstrumentId | None = None,
        boundary_liquidation: bool = False,
    ) -> BacktestResult:
        if not isinstance(self.config, BacktestConfig):
            raise DomainError("BacktestConfig is required")
        start_at, end_at = utc(start), utc(end)
        if start_at >= end_at:
            raise DomainError("Backtest requires start < end")
        if type(boundary_liquidation) is not bool:
            raise DomainError("boundary_liquidation must be boolean")
        if boundary_liquidation and len(self.snapshots) != 1:
            raise DomainError("Boundary liquidation initially requires exactly one instrument")
        instruments = {
            snapshot.instrument.instrument_id: snapshot.instrument for snapshot in self.snapshots
        }
        if len(instruments) != len(self.snapshots):
            raise DomainError("Only one snapshot per instrument is supported")
        if any(instrument.currency != self.config.currency for instrument in instruments.values()):
            raise DomainError("All backtest instruments must use the configured currency")
        if benchmark is not None and benchmark not in instruments:
            raise DomainError("Benchmark instrument has no configured snapshot")
        if isinstance(self.strategy, StrategyRunMetadata):
            self.strategy.begin_run()

        bars = tuple(
            sorted(
                (
                    bar
                    for snapshot in self.snapshots
                    for bar in self.store.query_bars(snapshot, start=start_at, end=end_at)
                    if bar.start < end_at
                    and (
                        bar.available_at < end_at
                        if boundary_liquidation
                        else bar.available_at <= end_at
                    )
                ),
                key=lambda bar: (bar.available_at, bar.instrument_id.value, bar.start),
            )
        )
        if boundary_liquidation and (
            not bars or bars[-1].available_at != bars[-1].end or bars[-1].end >= end_at
        ):
            raise DomainError(
                "Boundary liquidation requires a final close strictly inside the window "
                "with immediate publication"
            )
        frequencies: dict[InstrumentId, set[str]] = defaultdict(set)
        for bar in bars:
            frequencies[bar.instrument_id].add(bar.frequency)
        if any(len(values) != 1 for values in frequencies.values()):
            raise DomainError("Each instrument must use one bar frequency per run")

        clock = SimulationClock(start_at)
        feeds = {
            snapshot.instrument.instrument_id: DataFeed(self.store, snapshot, clock)
            for snapshot in self.snapshots
        }
        broker = HistoricalBroker(self.execution)
        ledger = PortfolioLedger(
            initial_cash=self.config.initial_cash,
            currency=self.config.currency,
            allow_short=self.config.allow_short,
            short_assumption=self.config.short_assumption,
        )
        reserved_buys: dict[OrderId, Decimal] = {}
        batch_available_cash = ledger.snapshot().cash

        def funds_guard(
            intent: OrderIntent,
            price: Decimal,
            quantity: Decimal,
            fee: Decimal,
            _observed_at: datetime,
        ) -> bool:
            nonlocal batch_available_cash, reserved_buys
            with localcontext(_context()):
                proposed_notional = quantity * price
                state = next(
                    order
                    for order in broker.orders.snapshots()
                    if order.intent.order_id == intent.order_id
                )
                prior_notional = sum(
                    (fill.quantity * fill.price for fill in state.fills), Decimal(0)
                )
                if prior_notional + proposed_notional > self.config.max_order_notional:
                    reserved_buys.pop(intent.order_id, None)
                    return False
                if intent.side is Side.BUY:
                    required = proposed_notional + fee
                    other_reserves = sum(
                        (
                            amount
                            for order_id, amount in reserved_buys.items()
                            if order_id != intent.order_id
                        ),
                        Decimal(0),
                    )
                    if (
                        intent.max_execution_price is not None
                        and price > intent.max_execution_price
                    ) or batch_available_cash - other_reserves < required:
                        reserved_buys.pop(intent.order_id, None)
                        return False
                    batch_available_cash -= required
                    reserve = reserved_buys.get(intent.order_id, Decimal(0))
                    reserved_buys[intent.order_id] = max(Decimal(0), reserve - required)
                return True

        broker.set_funds_guard(funds_guard)
        portfolio = ledger.snapshot()
        nav_points = [_nav_point(start_at, portfolio, None)]
        fills: list[Fill] = []
        benchmark_points: list[BenchmarkPoint] = []
        last_nav = self.config.initial_cash
        benchmark_base: Decimal | None = None
        benchmark_price: Decimal | None = None
        benchmark_value: Decimal | None = None

        index = 0
        while index < len(bars):
            observed_at = bars[index].available_at
            batch_available_cash = portfolio.cash
            batch: list[Bar] = []
            while index < len(bars) and bars[index].available_at == observed_at:
                batch.append(bars[index])
                index += 1
            clock.advance(observed_at)

            for bar in batch:
                bar_fills = broker.process_bar(bar, observed_at)
                for fill in bar_fills:
                    if fill.side is Side.BUY:
                        remaining = next(
                            (
                                order.remaining_quantity
                                for order in broker.orders.snapshots()
                                if order.intent.order_id == fill.order_id
                            ),
                            Decimal(0),
                        )
                        if remaining == 0:
                            reserved_buys.pop(fill.order_id, None)
                    ledger.apply_fill(to_ledger_fill(fill))
                    fills.append(fill)
                active_ids = {
                    order.intent.order_id
                    for order in broker.orders.snapshots()
                    if order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
                }
                for order_id in tuple(reserved_buys):
                    if order_id not in active_ids:
                        reserved_buys.pop(order_id, None)

            if boundary_liquidation and index == len(bars):
                # This contingent closing-auction instruction is scheduled from
                # the declared run boundary, never from the final observed price.
                # Quantity is the actual residual after ordinary final-bar fills.
                close_bar = batch[-1]
                for active in broker.orders.snapshots():
                    if active.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
                        broker.cancel(
                            active.intent.order_id, observed_at, "scheduled boundary close"
                        )
                reserved_buys.clear()
                batch_available_cash = ledger.snapshot().cash
                residual = next(
                    (
                        position
                        for position in ledger.snapshot().positions
                        if position.quantity != 0
                    ),
                    None,
                )
                if residual is not None:
                    instrument = instruments[residual.instrument_id]
                    with localcontext(_context()):
                        quantity = abs(residual.quantity)
                        used = sum((fill.quantity for fill in bar_fills), Decimal(0))
                        capacity = (
                            _floor_step(
                                close_bar.volume * self.execution.maximum_participation,
                                instrument.quantity_step,
                            )
                            - used
                        )
                    if close_bar.volume <= 0 or quantity > capacity:
                        raise DomainError("Insufficient remaining final-bar liquidation capacity")
                    side = Side.SELL if residual.quantity > 0 else Side.BUY
                    liquidation = OrderIntent(
                        OrderId("scheduled-boundary-close"),
                        instrument,
                        side,
                        TradeType.MARKET,
                        quantity,
                        close_bar.end,
                    )
                    # Dedicated scheduled-final-close lifecycle. Ordinary market
                    # orders keep M4 next-bar semantics and are never replayed.
                    broker.orders.submit(liquidation, close_bar.end)
                    with localcontext(_context()):
                        price = broker._fill_price(
                            liquidation, close_bar.close, None, quantity / close_bar.volume
                        )
                        fee = self.execution.commission.incremental_fee(
                            Decimal(0), quantity, Decimal(0)
                        )
                    if price <= 0 or not isinstance(fee, Decimal) or not fee.is_finite() or fee < 0:
                        raise DomainError("Invalid configured final-close execution price or fee")
                    if not funds_guard(liquidation, price, quantity, fee, observed_at):
                        raise DomainError("Final-close liquidation exceeds cash or notional limits")
                    closing_fill = Fill(
                        FillId("scheduled-boundary-close-fill"),
                        liquidation.order_id,
                        instrument,
                        side,
                        quantity,
                        close_bar.close,
                        price,
                        fee,
                        close_bar.end,
                        observed_at,
                    )
                    broker.orders._record_fill(
                        broker.orders._get(liquidation.order_id), closing_fill
                    )
                    ledger.apply_fill(to_ledger_fill(closing_fill))
                    fills.append(closing_fill)

            pre_mark_portfolio = ledger.snapshot()
            marked = False
            for bar in batch:
                if any(
                    position.instrument_id == bar.instrument_id and position.quantity != 0
                    for position in pre_mark_portfolio.positions
                ):
                    ledger.mark(bar.instrument_id, bar.close)
                    marked = True
                if benchmark == bar.instrument_id:
                    benchmark_price = bar.close
                    if benchmark_base is None:
                        benchmark_base = bar.close
                    with localcontext(_context()):
                        benchmark_value = benchmark_price / benchmark_base

            portfolio = ledger.snapshot() if marked else pre_mark_portfolio
            with localcontext(_context()):
                period_return = (
                    portfolio.nav / last_nav - Decimal(1)
                    if last_nav > 0 and portfolio.nav > 0
                    else None
                )
            nav_points.append(_nav_point(observed_at, portfolio, period_return))
            last_nav = portfolio.nav
            if (
                benchmark is not None
                and benchmark_price is not None
                and benchmark_value is not None
            ):
                benchmark_points.append(
                    BenchmarkPoint(observed_at, benchmark, benchmark_price, benchmark_value)
                )

            active_orders = tuple(
                order
                for order in broker.orders.snapshots()
                if order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
            )
            if boundary_liquidation and index == len(bars):
                continue
            strategy_context = StrategyContext(
                current_time=observed_at,
                portfolio=portfolio,
                active_orders=active_orders,
                _feeds=MappingProxyType(feeds),
                _instruments=MappingProxyType(instruments),
            )
            proposals: list[tuple[OrderIntent, Bar]] = []
            for bar in batch:
                returned = self.strategy.on_bar(strategy_context, bar)
                if returned is None or isinstance(returned, (str, bytes)):
                    raise DomainError("Strategy on_bar must return an iterable of OrderIntent")
                if not isinstance(returned, Iterable):
                    raise DomainError("Strategy on_bar must return an iterable of OrderIntent")
                for intent in returned:
                    if not isinstance(intent, OrderIntent):
                        raise DomainError("Strategy emitted a non-OrderIntent value")
                    proposals.append((intent, bar))

            for intent, source_bar in proposals:
                existing_ids = {order.intent.order_id for order in broker.orders.snapshots()}
                if intent.order_id in existing_ids:
                    raise DomainError("Strategy reused an order_id")
                reason = self._risk_reason(
                    intent,
                    source_bar,
                    observed_at,
                    ledger.snapshot(),
                    broker.orders.snapshots(),
                    instruments,
                )
                if reason is not None:
                    broker.reject_intent(intent, observed_at, reason)
                    continue
                if intent.side is Side.BUY and not self.config.allow_short:
                    reserve_price = self._reserve_price(intent, source_bar)
                    reserve_amount = self._reserve_amount(intent, reserve_price)
                    if self._cash_reservation_reason(
                        {**reserved_buys, intent.order_id: reserve_amount},
                        ledger.snapshot(),
                    ):
                        broker.reject_intent(
                            intent,
                            observed_at,
                            "buy order exceeds available cash after reserved commitments",
                        )
                        continue
                broker.submit(intent, accepted_at=observed_at)
                if intent.side is Side.BUY and not self.config.allow_short:
                    reserved_buys[intent.order_id] = reserve_amount

        orders = broker.orders.snapshots()
        trades = tuple(order.trade for order in orders if order.trade is not None)
        with localcontext(_context()):
            commission_cost = sum((fill.commission for fill in fills), Decimal(0))
            slippage_cost = sum(
                (
                    (
                        fill.price - fill.reference_price
                        if fill.side is Side.BUY
                        else fill.reference_price - fill.price
                    )
                    * fill.quantity
                    for fill in fills
                ),
                Decimal(0),
            )
        metadata: tuple[tuple[str, str], ...] = (
            ("allow_short", str(self.config.allow_short)),
            ("allow_zero_costs", str(self.config.allow_zero_costs)),
            (
                "cost_assumption",
                self.execution.zero_cost_assumption or "configured_costs",
            ),
            ("commission_model", repr(self.execution.commission)),
            ("currency", self.config.currency),
            ("dataset_snapshots", ",".join(snapshot.snapshot_id for snapshot in self.snapshots)),
            ("initial_cash", str(self.config.initial_cash)),
            ("max_participation", str(self.execution.maximum_participation)),
            ("max_order_notional", str(self.config.max_order_notional)),
            ("short_assumption", self.config.short_assumption or ""),
            ("slippage_model", repr(self.execution.slippage)),
            ("strategy", self.strategy.name),
            ("benchmark", benchmark.value if benchmark is not None else ""),
            ("start", start_at.isoformat()),
            ("end", end_at.isoformat()),
        )
        if boundary_liquidation:
            metadata += (("boundary_liquidation", "scheduled_final_close_v1"),)
        if isinstance(self.strategy, StrategyRunMetadata):
            metadata += self.strategy.run_metadata()
        return BacktestResult(
            strategy_name=self.strategy.name,
            start=start_at,
            end=end_at,
            nav=tuple(nav_points),
            orders=orders,
            fills=tuple(fills),
            trades=trades,
            commission_cost=commission_cost,
            slippage_cost=slippage_cost,
            benchmark=tuple(benchmark_points),
            metadata=tuple(sorted(metadata)),
        )

    def _risk_reason(
        self,
        intent: OrderIntent,
        bar: Bar,
        now: datetime,
        portfolio: PortfolioSnapshot,
        orders: tuple[OrderSnapshot, ...],
        instruments: Mapping[InstrumentId, InstrumentMeta],
    ) -> str | None:
        if intent.created_at > now:
            raise DomainError("Strategy order creation time is in the future")
        if intent.instrument.instrument_id not in instruments:
            return "instrument is outside the configured universe"
        if instruments[intent.instrument.instrument_id] != intent.instrument:
            return "instrument metadata does not match the configured snapshot"
        if intent.instrument.currency != self.config.currency:
            return "instrument currency mismatch"
        reference = max(
            price
            for price in (
                bar.close,
                intent.limit_price,
                intent.stop_price,
                intent.max_execution_price,
            )
            if price is not None
        )
        with localcontext(_context()):
            notional = reference * intent.quantity
        if notional > self.config.max_order_notional:
            return "order notional exceeds configured limit"
        if (
            intent.side is Side.BUY
            and not self.config.allow_short
            and intent.order_type in (TradeType.MARKET, TradeType.STOP)
            and intent.max_execution_price is None
        ):
            return "cash-constrained market buys require max_execution_price"
        if intent.side is Side.SELL and not self.config.allow_short:
            current = next(
                (
                    position.quantity
                    for position in portfolio.positions
                    if position.instrument_id == intent.instrument.instrument_id
                ),
                Decimal(0),
            )
            with localcontext(_context()):
                pending_sells = sum(
                    (
                        order.remaining_quantity
                        for order in orders
                        if order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
                        and order.intent.instrument.instrument_id == intent.instrument.instrument_id
                        and order.intent.side is Side.SELL
                    ),
                    Decimal(0),
                )
                available = max(Decimal(0), current - pending_sells)
            if intent.quantity > available:
                return "sell exceeds available long inventory"
        return None

    def _cash_reservation_reason(
        self,
        reserves: Mapping[OrderId, Decimal],
        portfolio: PortfolioSnapshot,
    ) -> bool:
        with localcontext(_context()):
            total = sum(reserves.values(), Decimal(0))
        return total > max(Decimal(0), portfolio.cash)

    def _reserve_amount(self, intent: OrderIntent, price: Decimal) -> Decimal:
        if not price.is_finite():
            raise DomainError("Cash-constrained buys require a finite reservation price")
        with localcontext(_context()):
            amount = price * intent.quantity
            amount += self.execution.commission.incremental_fee(
                Decimal(0), intent.quantity, Decimal(0)
            )
            return amount

    def _reserve_price(self, intent: OrderIntent, bar: Bar) -> Decimal:
        if intent.order_type in (TradeType.MARKET, TradeType.STOP) and intent.side is Side.BUY:
            return intent.max_execution_price or Decimal("Infinity")
        candidates = [
            price for price in (intent.limit_price, intent.stop_price) if price is not None
        ]
        if not candidates:
            candidates.append(bar.close)
        with localcontext(_context()):
            return max(candidates)
