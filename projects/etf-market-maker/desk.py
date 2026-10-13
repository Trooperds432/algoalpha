"""Synthetic, paper-only ETF desk. All amounts use one declared base currency.

No broker adapters or network code. NAV valuation has no market-price argument.
Weighted reference-price mode represents a fixed-share basket initially worth base_nav.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np


def number(value: Any, name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"Invalid {name}")
    return result


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return result


def fair_value(basket: dict, as_of: str, max_age_seconds: float = 60) -> dict:
    """base_nav * sum(weight * price * fx / (reference_price * reference_fx)).

    Add net_cash_per_share, subtract accrued_fee_per_share. Weights are initial
    capital weights, NOT quantities or current target weights. FX is base currency
    per unit of asset currency. Reference prices and FX are fixed at inception.
    All asset prices/FX are timestamped together. Unknown input fields rejected.
    """
    allowed = {"base_nav", "currency", "underlying_assets", "net_cash_per_share", "accrued_fee_per_share"}
    if set(basket) - allowed:
        raise ValueError("Unknown basket fields (ETF market price must be supplied separately)")
    if not isinstance(basket.get("currency"), str) or not basket["currency"]:
        raise ValueError("A base currency is required")
    now = timestamp(as_of)
    max_age_seconds = number(max_age_seconds, "max_age_seconds", 0)
    nav = number(basket["base_nav"], "base_nav", 0.000001)
    assets = basket["underlying_assets"]
    if not assets:
        raise ValueError("Empty basket")
    total_weight, total, contributions, seen = 0.0, 0.0, [], set()
    for asset in assets:
        symbol = asset["symbol"]
        if not symbol or symbol in seen:
            raise ValueError("Constituent symbols must be nonempty and unique")
        seen.add(symbol)
        age = (now - timestamp(asset["timestamp"])).total_seconds()
        if not 0 <= age <= max_age_seconds:
            raise ValueError(f"Stale or future constituent/FX observation: {symbol}")
        weight = number(asset["weight"], "weight", 0)
        price = number(asset["price"], "price", 0.000001)
        reference = number(asset["reference_price"], "reference_price", 0.000001)
        fx = number(asset["fx"], "fx", 0.000001)
        reference_fx = number(asset["reference_fx"], "reference_fx", 0.000001)
        contribution = nav * weight * price * fx / (reference * reference_fx)
        contributions.append({"symbol": symbol, "value_per_etf": contribution})
        total_weight += weight
        total += contribution
    if not math.isclose(total_weight, 1.0, abs_tol=1e-9, rel_tol=0):
        raise ValueError("Weights must sum to one; no silent normalisation")
    total += number(basket.get("net_cash_per_share", 0), "net cash")
    total -= number(basket.get("accrued_fee_per_share", 0), "accrued fee", 0)
    if total <= 0:
        raise ValueError("NAV must be positive")
    return {"fair_value": total, "currency": basket["currency"], "as_of": as_of,
            "method": "fixed_share_reference_basket", "contributions": contributions}


def dislocation(valuation: dict, market_price: float, round_trip_cost: float) -> dict:
    market = number(market_price, "market_price", 0.000001)
    costs = number(round_trip_cost, "round_trip_cost", 0)
    value = number(valuation["fair_value"], "fair_value", 0.000001)
    deviation = market - value
    return {"fair_value": value, "market_price": market, "dislocation": deviation,
            "dislocation_bps": 10000 * deviation / value,
            "estimated_net_edge_per_share": abs(deviation) - costs,
            "candidate": abs(deviation) > costs,
            "direction": "sell_etf_buy_basket" if deviation > 0 else "buy_etf_sell_basket",
            "qualification": "relative-value candidate, not guaranteed arbitrage; no creation/redemption access"}


def mean_reversion(past_deviations: list[float]) -> dict:
    """OLS AR(1) on already observed deviations only. No future prices allowed."""
    x = np.asarray(past_deviations, dtype=float)
    if len(x) < 30 or not np.isfinite(x).all():
        return {"eligible": False, "reason": "At least 30 finite past deviations required"}
    if np.std(x[:-1]) < 1e-12:
        return {"eligible": False, "reason": "No variation"}
    design = np.column_stack([np.ones(len(x) - 1), x[:-1]])
    intercept, phi = np.linalg.lstsq(design, x[1:], rcond=None)[0]
    eligible = 0 < phi < 1
    return {"eligible": bool(eligible), "phi": float(phi), "intercept": float(intercept),
            "half_life_events": float(-math.log(2) / math.log(phi)) if eligible else None,
            "samples": len(x), "note": "Descriptive fit only; not a stationarity or profitability test"}


def flow_metrics(buy_volume: float, sell_volume: float, bid_depth: float,
                 ask_depth: float, previous_depth: float) -> dict:
    buy, sell, bid, ask, previous = [number(v, "flow/depth", 0) for v in
                                   (buy_volume, sell_volume, bid_depth, ask_depth, previous_depth)]
    volume = buy + sell
    depth = bid + ask
    return {"buy_pressure": buy / volume if volume else 0,
            "sell_pressure": sell / volume if volume else 0,
            "order_imbalance": (buy - sell) / volume if volume else 0,
            "book_imbalance": (bid - ask) / depth if depth else 0,
            "liquidity_depletion": max(0, 1 - depth / previous) if previous else 0}


@dataclass(frozen=True)
class Limits:
    initial_capital: float = 1_000_000
    max_gross_notional: float = 500_000
    max_net_notional: float = 100_000
    max_inventory: int = 5000
    max_loss: float = 20_000
    max_drawdown_pct: float = 25
    hedge_trigger_units: int = 500
    residual_target_units: int = 200
    hedge_participation: float = 0.1
    fee_bps: float = 0.5
    slippage_bps: float = 1.0
    tick: float = 0.01

    def __post_init__(self):
        for key, value in asdict(self).items():
            number(value, key, 0)
        if self.initial_capital <= 0 or self.tick <= 0 or not 0 < self.hedge_participation <= 1:
            raise ValueError("Invalid capital/tick/participation")
        if not 0 < self.max_drawdown_pct <= 100:
            raise ValueError("Invalid drawdown ceiling")
        if self.residual_target_units >= self.hedge_trigger_units:
            raise ValueError("Residual target must be below trigger")


def dynamic_quote(value: float, inventory: int, volatility: float, liquidity: float,
                  imbalance: float, limits: Limits, predicted_spread: float = 0.10) -> dict:
    """Transparent heuristic, not a claimed optimal or calibrated AS solution.

    volatility: fractional next-event stddev; liquidity: units per event.
    Wider spread for higher volatility, scarce depth, adverse-selection pressure.
    Long inventory lowers the reservation price; short inventory raises it.
    """
    value = number(value, "value", 0.000001)
    volatility = number(volatility, "volatility", 0)
    liquidity = number(liquidity, "liquidity", 0)
    imbalance = number(imbalance, "imbalance")
    if abs(imbalance) > 1:
        raise ValueError("Imbalance outside [-1,1]")
    inventory = number(inventory, "inventory")
    predicted_spread = number(predicted_spread, "predicted_spread", 0)
    cost_floor = value * (limits.fee_bps + limits.slippage_bps) / 10000
    half = max(limits.tick, predicted_spread / 2, cost_floor)
    half += 0.5 * value * volatility + 0.02 * abs(imbalance) + 20 / max(liquidity, 1)
    skew = (inventory / max(limits.max_inventory, 1)) * max(0.05, 2 * value * volatility)
    centre = value - skew
    bid = math.floor((centre - half) / limits.tick + 1e-10) * limits.tick
    ask = math.ceil((centre + half) / limits.tick - 1e-10) * limits.tick
    if bid <= 0 or not bid < ask:
        return {"enabled": False, "reason": "Invalid or nonpositive quote"}
    return {"enabled": True, "bid": round(bid, 8), "ask": round(ask, 8),
            "reservation_price": centre, "spread": ask - bid,
            "bid_enabled": inventory < limits.max_inventory and liquidity > 0,
            "ask_enabled": inventory > -limits.max_inventory and liquidity > 0}


def hedge_decision(inventory: int, hedge_units: int, price: float, liquidity: float,
                   limits: Limits) -> dict:
    """Hedge in synthetic basket ETF-equivalent units. Does not erase ETF inventory."""
    net = inventory + hedge_units
    if abs(net) < limits.hedge_trigger_units:
        return {"inventory": inventory, "action": "hold", "size": 0, "signed_size": 0,
                "residual_delta_units": net}
    desired = -net + math.copysign(limits.residual_target_units, net)
    max_size = max(0, math.floor(number(liquidity, "liquidity", 0) * limits.hedge_participation))
    signed = int(math.copysign(min(abs(desired), max_size), desired))
    # Gross limit counts both legs: a hedge reduces delta but can increase capital use.
    affordable = max(0, int(limits.max_gross_notional / number(price, "price", 0.000001)) - abs(inventory))
    target_hedge = max(-affordable, min(affordable, hedge_units + signed))
    signed = target_hedge - hedge_units
    signed = int(math.copysign(min(abs(signed), max_size), signed)) if signed else 0
    if abs(hedge_units + signed) > affordable:
        signed = 0
    # Never turn a requested hedge into an exposure-increasing trade.
    if abs(net + signed) >= abs(net):
        signed = 0
    return {"inventory": inventory, "action": "hedge" if signed else "blocked",
            "size": abs(signed), "signed_size": signed,
            "side": "buy_basket" if signed > 0 else "sell_basket" if signed < 0 else None,
            "residual_delta_units": net + signed,
            "reason": "hedge within liquidity/gross cap" if signed else "Insufficient capacity"}


@dataclass
class PaperLedger:
    limits: Limits
    cash: float = field(init=False)
    inventory: int = 0
    hedge_units: int = 0
    fees: float = 0.0
    slippage: float = 0.0
    high_water: float = field(init=False)
    halted: bool = False
    halt_reason: str | None = None
    fills: list = field(default_factory=list)

    def __post_init__(self):
        self.cash = self.limits.initial_capital
        self.high_water = self.cash

    def equity(self, etf_price: float, basket_price: float) -> float:
        return self.cash + self.inventory * etf_price + self.hedge_units * basket_price

    def check(self, etf_price: float, basket_price: float) -> dict:
        etf_price = number(etf_price, "etf_price", 0.000001)
        basket_price = number(basket_price, "basket_price", 0.000001)
        equity = self.equity(etf_price, basket_price)
        self.high_water = max(self.high_water, equity)
        drawdown = 100 * (self.high_water - equity) / self.high_water
        gross = abs(self.inventory * etf_price) + abs(self.hedge_units * basket_price)
        net = self.inventory * etf_price + self.hedge_units * basket_price
        reasons = []
        if equity <= self.limits.initial_capital - self.limits.max_loss:
            reasons.append("loss_limit")
        if drawdown >= self.limits.max_drawdown_pct:
            reasons.append("drawdown_limit")
        if gross > self.limits.max_gross_notional + 1e-8:
            reasons.append("gross_limit")
        if abs(net) > self.limits.max_net_notional + 1e-8:
            reasons.append("net_limit")
        if abs(self.inventory) > self.limits.max_inventory:
            reasons.append("inventory_limit")
        if reasons:
            self.halted, self.halt_reason = True, ",".join(reasons)
        return {"equity": equity, "gross_notional": gross, "net_notional": net,
                "drawdown_pct": drawdown, "halted": self.halted, "halt_reason": self.halt_reason}

    def fill(self, instrument: str, signed_size: int, signal_price: float, etf_mark: float,
             basket_mark: float, at: str, available: int, reason: str) -> bool:
        """Atomic all-or-reject risk gate. Aggressive basket fills pay slippage.

        Synthetic ETF limit fills execute at the quoted limit (no price improvement).
        Fees apply to both legs; position MTM includes spread/adverse selection.
        """
        if instrument not in {"ETF", "BASKET"}:
            raise ValueError("Unknown instrument")
        if isinstance(signed_size, bool) or not isinstance(signed_size, int) or signed_size == 0:
            raise ValueError("Signed size must be a nonzero integer")
        for name, value in (("signal", signal_price), ("etf_mark", etf_mark), ("basket_mark", basket_mark)):
            number(value, name, 0.000001)
        timestamp(at)
        number(available, "available", 0)
        if abs(signed_size) > available:
            return False
        before = self.check(etf_mark, basket_mark)
        if self.halted:
            return False
        inv = self.inventory + (signed_size if instrument == "ETF" else 0)
        hedge = self.hedge_units + (signed_size if instrument == "BASKET" else 0)
        gross = abs(inv * etf_mark) + abs(hedge * basket_mark)
        net = inv * etf_mark + hedge * basket_mark
        if abs(inv) > self.limits.max_inventory or gross > self.limits.max_gross_notional or abs(net) > self.limits.max_net_notional:
            return False
        slip = signal_price * self.limits.slippage_bps / 10000 if instrument == "BASKET" else 0.0
        price = signal_price + math.copysign(slip, signed_size)
        fee = abs(signed_size) * price * self.limits.fee_bps / 10000
        cash = self.cash - signed_size * price - fee
        candidate_equity = cash + inv * etf_mark + hedge * basket_mark
        if candidate_equity <= self.limits.initial_capital - self.limits.max_loss:
            return False
        if 100 * (self.high_water - candidate_equity) / self.high_water >= self.limits.max_drawdown_pct:
            return False
        self.cash, self.inventory, self.hedge_units = cash, inv, hedge
        self.fees += fee
        self.slippage += abs(signed_size) * slip
        after = self.check(etf_mark, basket_mark)
        self.fills.append({"timestamp": at, "symbol": instrument, "side": "buy" if signed_size > 0 else "sell",
                           "signed_size": signed_size, "theoretical_size": abs(signed_size),
                           "signal_price": signal_price, "simulated_fill": price, "fees": fee,
                           "slippage": abs(signed_size) * slip, "exit": None,
                           "mtm_pnl": after["equity"] - self.limits.initial_capital,
                           "equity_before": before["equity"], "equity_after": after["equity"],
                           "inventory": inv, "hedge_units": hedge, "reason": reason,
                           "data_source": "seeded synthetic generator", "mode": "paper"})
        return True


def synthetic_events(seed: int = 7, count: int = 1000) -> list[dict]:
    """One-second events, three constituents, autocorrelated ETF-basket basis.

    Entire dataset is a mathematical toy, not SPY/QQQ/TLT/GLD/BTC history.
    No future feature values passed to the quoting engine.
    """
    if not isinstance(count, int) or count < 60:
        raise ValueError("At least 60 events required")
    rng = random.Random(seed)
    prices, basis, events, previous_depth = [100.0, 100.0, 100.0], 0.0, [], 4000.0
    start = datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)
    for i in range(count):
        regime = (i * 4) // count
        vol = [0.00010, 0.00035, 0.00016, 0.00065][regime]
        common = rng.gauss(0, vol)
        prices = [p * math.exp(common + rng.gauss(0, vol / 3)) for p in prices]
        basis = 0.92 * basis + rng.gauss(0, 0.012 + vol * 40)
        at = (start + timedelta(seconds=i)).isoformat()
        basket = {"base_nav": 100, "currency": "USD", "underlying_assets": [
            {"symbol": f"SYNTH-{j+1}", "weight": w, "price": p, "reference_price": 100,
             "fx": 1, "reference_fx": 1, "timestamp": at}
            for j, (w, p) in enumerate(zip([0.25, 0.25, 0.5], prices))]}
        value = fair_value(basket, at)["fair_value"]
        liquidity = max(100, int(rng.gauss(4000 if regime != 3 else 1000, 300)))
        buy, sell = rng.randint(0, 400), rng.randint(0, 400)
        flow = flow_metrics(buy, sell, liquidity / 2, liquidity / 2, previous_depth)
        previous_depth = liquidity
        events.append({"timestamp": at, "basket": basket, "market_price": value + basis,
                       "buy_volume": buy, "sell_volume": sell, "liquidity": liquidity,
                       "observed_spread": 0.06 + vol * 100, "flow": flow,
                       "regime": ["calm", "volatile", "recovery", "liquidity_shock"][regime]})
    return events


def simulate(events: list[dict], limits: Limits | None = None, seed: int = 9,
             forecasts: list[dict] | None = None) -> dict:
    """Quotes from t-1 face aggressive orders at t (one-event latency).

    Fill probability declines with quote distance, is capped at 80%, has 50%
    queue participation, and never exceeds event liquidity or customer volume.
    It is an assumption, not empirically estimated queue position.
    Forecast records for the NEXT event must be available by quote decision time.
    """
    limits = limits or Limits()
    ledger, rng, quotes, equity, deviations = PaperLedger(limits), random.Random(seed), [], [], []
    past_returns, previous_value, previous_quote = [], None, None
    rejected = 0
    for index, event in enumerate(events):
        at = event["timestamp"]
        if index and timestamp(at) <= timestamp(events[index-1]["timestamp"]):
            raise ValueError("Event timestamps must increase strictly")
        value = fair_value(event["basket"], at)["fair_value"]
        market = number(event["market_price"], "market", 0.000001)
        liquidity = int(number(event["liquidity"], "liquidity", 0))
        status = ledger.check(market, value)
        if previous_quote and previous_quote["enabled"] and not status["halted"]:
            for sign, side, demand_key in [(1, "bid", "sell_volume"), (-1, "ask", "buy_volume")]:
                q = previous_quote
                if not q[side + "_enabled"]:
                    continue
                distance = max(0, (market - q["bid"]) if sign > 0 else (q["ask"] - market))
                probability = min(0.8, 0.65 * math.exp(-distance / max(event["observed_spread"], 0.01)))
                size = min(100, int(event[demand_key] * 0.5), int(liquidity * 0.05))
                if size > 0 and rng.random() < probability:
                    if not ledger.fill("ETF", sign * size, q[side], market, value, at, liquidity,
                                       "prior-event quote; stochastic queue fill"):
                        rejected += 1
        hedge = hedge_decision(ledger.inventory, ledger.hedge_units, value, liquidity, limits)
        if hedge["signed_size"] and not ledger.halted:
            if not ledger.fill("BASKET", hedge["signed_size"], value, market, value, at,
                               int(liquidity * limits.hedge_participation), "delta hedge"):
                rejected += 1
        if previous_value is not None:
            past_returns.append(value / previous_value - 1)
        previous_value = value
        vol = float(np.std(past_returns[-60:], ddof=1)) if len(past_returns) >= 2 else 0.0002
        predicted_spread, predicted_liquidity = event["observed_spread"], liquidity
        if forecasts is not None and forecasts[index] is not None:
            forecast = forecasts[index]
            if (timestamp(forecast["as_of"]) > timestamp(at)
                    or timestamp(forecast["target_timestamp"]) <= timestamp(at)):
                raise ValueError("Forecast information leakage")
            vol = number(forecast["volatility"], "forecast volatility", 0)
            predicted_spread = number(forecast["spread_width"], "forecast spread", 0)
            predicted_liquidity = min(liquidity, number(forecast["liquidity"], "forecast liquidity", 0))
        previous_quote = dynamic_quote(value, ledger.inventory, vol, predicted_liquidity,
                                       event["flow"]["order_imbalance"], limits, predicted_spread)
        if ledger.halted:
            previous_quote = {"enabled": False, "reason": ledger.halt_reason}
        deviations.append(market - value)
        status = ledger.check(market, value)
        quotes.append({"timestamp": at, "fair_value": value, "market_price": market,
                       "inventory": ledger.inventory, "hedge_units": ledger.hedge_units,
                       "regime": event["regime"], **previous_quote})
        equity.append({"timestamp": at, "regime": event["regime"], **status})
    if not events:
        raise ValueError("No events")
    net_pnl = equity[-1]["equity"] - limits.initial_capital
    return {"mode": "paper", "data_kind": "synthetic", "currency": "USD",
            "start": events[0]["timestamp"], "end": events[-1]["timestamp"],
            "summary": {"net_mtm_pnl": net_pnl, "fees": ledger.fees, "slippage": ledger.slippage,
                        "gross_pnl_before_explicit_costs": net_pnl + ledger.fees + ledger.slippage,
                        "fills": len(ledger.fills), "rejected_orders": rejected,
                        "final_inventory": ledger.inventory, "final_hedge_units": ledger.hedge_units,
                        "max_drawdown_pct": max(e["drawdown_pct"] for e in equity),
                        "halted": ledger.halted, "halt_reason": ledger.halt_reason,
                        "sharpe": None, "sharpe_note": "Intraday toy sample: no credible annualised Sharpe",
                        "liquidated": False, "note": "Open positions marked to market; no realised-return claim"},
            "mean_reversion": mean_reversion(deviations), "fills": ledger.fills,
            "quotes": quotes, "equity": equity, "limits": asdict(limits)}
