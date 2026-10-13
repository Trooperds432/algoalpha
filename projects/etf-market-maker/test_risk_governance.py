import unittest
from risk_governance import risk_report, stress_report, validate_strategy, allocate_capital


class GovernanceTests(unittest.TestCase):
    def evidence(self):
        return dict(data_kind='historical', historical_months=6, oos_documented=True,
                    costs_documented=True, paper_days=30, actual_paper_days_documented=True,
                    sharpe=2.1, max_drawdown=.09, feature_timestamps=[1, 2],
                    decision_timestamps=[2, 3], regimes=['bull', 'bear', 'sideways'],
                    net_pnl_after_costs=10, capacity_gbp=100_000_000,
                    proposed_daily_volume_gbp=1_000_000, market_daily_volume_gbp=100_000_000,
                    max_participation=.05, fx_gbp_per_quote=.75,
                    fx_assumption='Fixed GBP/USD conversion in capacity estimate',
                    benchmark='basket total return', sharpe_convention='daily sqrt(252), zero rf',
                    independent_backtest=True, independent_risk_review=True)

    def test_var_known_losses_and_tail_uncertainty(self):
        r = risk_report([100], [[-.01], [-.02], [-.03], [-.04]])
        self.assertEqual(r['var']['95'], 4)
        self.assertEqual(r['es']['99.9'], 4)
        self.assertTrue(r['tail_uncertainty']['99.9']['insufficient_tail_sample'])
        with self.assertRaises(ValueError):
            risk_report([100], [[float('nan')]])

    def test_stresses_both_directions_and_costs(self):
        r = stress_report(100, -100, 100, 100, .1, 1000)
        self.assertEqual(len(r['scenarios']), 10)
        for s in r['scenarios']:
            self.assertGreater(s['liquidation_cost'], 0)
            self.assertLess(s['net_pnl'], s['mark_to_market_pnl'])
        shock = next(s for s in r['scenarios'] if s['name'] == 'liquidity_shock_down')
        self.assertEqual(shock['spread_multiplier'], 3)
        self.assertEqual(shock['liquidity_multiplier'], .5)

    def test_validation_pass_and_fail_closed(self):
        threshold = dict(min_sharpe=2, max_drawdown=.1)
        self.assertTrue(validate_strategy(self.evidence(), threshold)['approval'])
        for key in self.evidence():
            e = self.evidence(); del e[key]
            self.assertFalse(validate_strategy(e, threshold)['approval'], key)
        for patch in [dict(data_kind='synthetic'), dict(sharpe=float('nan')),
                      dict(feature_timestamps=[3, 4]), dict(paper_days=29),
                      dict(proposed_daily_volume_gbp=10_000_000), dict(net_pnl_after_costs=-1),
                      dict(max_drawdown=.11), dict(regimes=['bull'])]:
            self.assertFalse(validate_strategy(self.evidence() | patch, threshold)['approval'])
        self.assertFalse(validate_strategy(self.evidence(), {})['approval'])

    def test_capital_veto_and_budget(self):
        votes = dict(quant='buy', fundamental='neutral', risk='approve', execution='buy', validation='approved')
        proposals = [dict(name='a', requested_capital=100, votes=votes),
                     dict(name='b', requested_capital=100, risk_cap=20, votes=votes | {'risk':'reduce_size'}),
                     dict(name='c', requested_capital=100, votes=votes | {'validation':'rejected'}),
                     dict(name='d', requested_capital=100, votes=votes | {'risk':'veto'}),
                     dict(name='e', requested_capital=100, votes={})]
        r = allocate_capital(proposals, 60)
        self.assertAlmostEqual(r['allocated_capital'], 60)
        self.assertLessEqual(r['allocations'][1]['allocated_capital'], 20)
        self.assertTrue(all(a['allocated_capital'] == 0 for a in r['allocations'][2:]))


if __name__ == '__main__':
    unittest.main()
