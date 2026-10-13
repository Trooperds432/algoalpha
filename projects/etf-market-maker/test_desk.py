import copy
import unittest
from dataclasses import replace

from desk import (Limits, PaperLedger, dislocation, dynamic_quote, fair_value,
                  flow_metrics, hedge_decision, mean_reversion, simulate, synthetic_events)


class DeskTests(unittest.TestCase):
    def basket(self):
        return {"base_nav": 100, "currency": "USD", "underlying_assets": [
            {"symbol": str(i), "weight": w, "price": p, "reference_price": 100,
             "fx": 1, "reference_fx": 1, "timestamp": "2025-01-01T00:00:00Z"}
            for i, (w, p) in enumerate([(0.25, 100.4), (0.25, 100.2), (0.5, 100.2)])]}

    def test_independent_nav_and_deviation(self):
        v = fair_value(self.basket(), "2025-01-01T00:00:00Z")
        self.assertAlmostEqual(v["fair_value"], 100.25)
        self.assertAlmostEqual(dislocation(v, 100.33, 0.1)["dislocation"], 0.08)
        self.assertFalse(dislocation(v, 100.33, 0.1)["candidate"])
        b = self.basket(); b["market_price"] = 100.33
        with self.assertRaises(ValueError): fair_value(b, "2025-01-01T00:00:00Z")

    def test_nav_fx_cash_and_validation(self):
        b = self.basket(); b["underlying_assets"][0]["fx"] = 2
        b["net_cash_per_share"] = 1; b["accrued_fee_per_share"] = .25
        self.assertAlmostEqual(fair_value(b, "2025-01-01T00:00:00Z")["fair_value"], 126.1)
        with self.assertRaises(ValueError): fair_value(b, "2025-01-02T00:00:00Z")
        with self.assertRaises(ValueError): fair_value(b, "2024-12-31T23:59:59Z")
        b["underlying_assets"][0]["weight"] = .5
        with self.assertRaises(ValueError): fair_value(b, "2025-01-01T00:00:00Z")

    def test_weights_only_cannot_price(self):
        with self.assertRaises((ValueError, KeyError)):
            fair_value({"underlying_assets": [{"weight": 1}]}, "2025-01-01T00:00:00Z")

    def test_quote_sign_widening_and_inventory_sides(self):
        limits = Limits()
        normal = dynamic_quote(100, 0, .001, 5000, 0, limits)
        long = dynamic_quote(100, 5000, .001, 5000, 0, limits)
        short = dynamic_quote(100, -5000, .001, 5000, 0, limits)
        high = dynamic_quote(100, 0, .005, 5000, 0, limits)
        self.assertLess(long["bid"], normal["bid"])
        self.assertGreater(short["ask"], normal["ask"])
        self.assertFalse(long["bid_enabled"]); self.assertFalse(short["ask_enabled"])
        self.assertGreater(high["spread"], normal["spread"])
        self.assertLess(normal["bid"], normal["ask"])

    def test_short_5000_hedges_4800_without_erasing_inventory(self):
        limits = replace(Limits(), max_gross_notional=2_000_000)
        h = hedge_decision(-5000, 0, 100, 100_000, limits)
        self.assertEqual(h["signed_size"], 4800)
        self.assertEqual(h["inventory"], -5000)
        self.assertEqual(h["residual_delta_units"], -200)
        self.assertLessEqual(hedge_decision(-5000, 0, 100, 1000, limits)["size"], 100)
        # An already-over-capacity book must not create a hedge exceeding depth.
        constrained = hedge_decision(4500, 4000, 100, 100, Limits())
        self.assertLessEqual(constrained["size"], 10)

    def test_ledger_roundtrip_fees_and_atomic_rejection(self):
        ledger = PaperLedger(replace(Limits(), slippage_bps=0))
        self.assertTrue(ledger.fill("ETF", 10, 99, 100, 100, "2025-01-01T00:00:00Z", 100, "test"))
        self.assertTrue(ledger.fill("ETF", -10, 101, 100, 100, "2025-01-01T00:00:01Z", 100, "test"))
        self.assertAlmostEqual(ledger.equity(100, 100) - 1_000_000, 19.9)
        before = copy.deepcopy(ledger.__dict__)
        self.assertFalse(ledger.fill("ETF", 6000, 100, 100, 100, "2025-01-01T00:00:02Z", 10000, "bad"))
        self.assertEqual(ledger.cash, before["cash"])
        self.assertEqual(ledger.fills, before["fills"])

    def test_drawdown_latches_halt(self):
        ledger = PaperLedger(replace(Limits(), max_loss=100))
        self.assertTrue(ledger.fill("ETF", 100, 100, 100, 100, "2025-01-01T00:00:00Z", 100, "test"))
        self.assertTrue(ledger.check(90, 100)["halted"])
        self.assertTrue(ledger.check(100, 100)["halted"])
        self.assertFalse(ledger.fill("ETF", 1, 100, 100, 100, "2025-01-01T00:00:01Z", 100, "halted"))

    def test_flow_and_no_variation(self):
        f = flow_metrics(300, 100, 100, 100, 400)
        self.assertEqual(f["order_imbalance"], .5)
        self.assertEqual(f["liquidity_depletion"], .5)
        self.assertFalse(mean_reversion([1]*40)["eligible"])

    def test_reproducibility_and_future_mutation_cannot_change_past(self):
        events = synthetic_events(count=100)
        first = simulate(events)
        self.assertEqual(first, simulate(events))
        changed = copy.deepcopy(events)
        for e in changed[70:]: e["market_price"] += 1
        second = simulate(changed)
        self.assertEqual(first["quotes"][:70], second["quotes"][:70])
        self.assertTrue(all(first["fills"][i]["timestamp"] > events[0]["timestamp"] for i in range(len(first["fills"]))))
        self.assertEqual(first["summary"]["sharpe"], None)


if __name__ == "__main__": unittest.main()
