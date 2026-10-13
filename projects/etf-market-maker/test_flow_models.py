import json
import importlib.util
import unittest

import numpy as np

from flow_models import order_flow_metrics, prepare_windows, run_forecast_demo, synthetic_flow


class FlowTests(unittest.TestCase):
    def test_pressure_and_depletion(self):
        result = order_flow_metrics(90, 30, 50, 100)
        self.assertEqual(result['order_imbalance'], 0.5)
        self.assertEqual(result['liquidity_depletion'], 0.5)
        self.assertEqual(order_flow_metrics(0, 0, 0, 0)['order_imbalance'], 0)
        with self.assertRaises(ValueError):
            order_flow_metrics(-1, 2, 3, 4)

    def test_split_purges_entire_windows(self):
        data = synthetic_flow(360, 4)
        result = prepare_windows(data, lookback=12)
        previous_end = -1
        for part in result['splits'].values():
            self.assertGreater(part['input_start'][0], previous_end)
            self.assertTrue(np.all(part['input_end'] < part['target_index']))
            previous_end = int(part['target_index'][-1])

    def test_future_mutation_cannot_change_training(self):
        data = synthetic_flow(360, 4)
        first = prepare_windows(data)
        data[216:] *= 1000
        second = prepare_windows(data)
        np.testing.assert_array_equal(first['mean'], second['mean'])
        np.testing.assert_array_equal(first['scale'], second['scale'])
        np.testing.assert_array_equal(first['splits']['train']['x'], second['splits']['train']['x'])

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'optional torch environment required')
    def test_real_models_produce_reproducible_out_of_sample_metrics(self):
        one = run_forecast_demo(seed=3, epochs=2, n=240)
        two = run_forecast_demo(seed=3, epochs=2, n=240)
        self.assertEqual(one, two)
        self.assertEqual(set(one['models']), {'LSTM', 'Transformer', 'ARIMA-LSTM'})
        self.assertNotIn('price', one['targets'])
        for model in one['models'].values():
            self.assertEqual(set(model['test_mae']), set(one['targets']))
            self.assertTrue(all(np.isfinite(x) and x >= 0 for x in model['test_mae'].values()))
        json.dumps(one, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
