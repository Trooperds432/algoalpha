import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from desk import simulate, synthetic_events
from run import run, feature_matrix
from flow_models import prepare_windows


class IntegrationTests(unittest.TestCase):
    def test_seven_deliverables_and_veto_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            output, report = run(seed=12, count=240, epochs=2, output_root=temp)
            self.assertFalse(report['validation']['approval'])
            self.assertEqual(report['capital']['allocated_capital'], 0)
            self.assertEqual(set(report['order_flow']['models']), {'LSTM','Transformer','ARIMA-LSTM'})
            for name in ['fair-value.json','order-flow.json','inventory.json','quotes.json',
                         'risk.json','stress.json','validation.json','capital.json','report.html',
                         'agent-messages.jsonl','fills.csv','equity.csv','manifest.json','synthetic-events.jsonl']:
                self.assertTrue((output/name).is_file(), name)
            self.assertEqual(len(list((output/'models').glob('*.pt'))), 3)
            messages = [json.loads(line) for line in (output/'agent-messages.jsonl').read_text().splitlines()]
            self.assertEqual(messages[-1]['recipient'], 'Reporting Agent')
            self.assertTrue(all(m['sender_agent_id'] and m['recipient_agent_id'] for m in messages))
            self.assertTrue(all(r['gross_notional'] <= report['simulation']['limits']['max_gross_notional']
                                for r in report['simulation']['equity']))
            json.dumps(report, allow_nan=False)

    def test_future_forecasts_rejected(self):
        events=synthetic_events(count=120)
        forecasts=[None]*120
        forecasts[2]={'as_of':events[3]['timestamp'],'target_timestamp':events[4]['timestamp'],
                      'spread_width':.1,'liquidity':100,'volatility':.001}
        with self.assertRaisesRegex(ValueError,'leakage'):
            simulate(events,forecasts=forecasts)

    def test_features_are_point_in_time_and_split_disjoint(self):
        events=synthetic_events(count=240)
        data=feature_matrix(events)
        before=prepare_windows(data)
        for event in events[200:]:
            event['basket']['underlying_assets'][0]['price']*=100
        after=prepare_windows(feature_matrix(events))
        self.assertTrue((before['splits']['train']['x']==after['splits']['train']['x']).all())


if __name__=='__main__': unittest.main()
