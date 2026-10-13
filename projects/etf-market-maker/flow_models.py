"""Reproducible synthetic, one-event-ahead liquidity forecasts; never price forecasts.

Optional CPU PyTorch is needed only for run_forecast_demo. No data feeds or accounts.
ARIMA-LSTM uses conditional least-squares ARIMA(1,1,0) plus learned residuals.
"""
from __future__ import annotations

import copy
import json
import math
import numpy as np

TARGETS = ('spread_width', 'displayed_liquidity', 'volatility')


def order_flow_metrics(buy_volume, sell_volume, current_depth, previous_depth):
    values = [buy_volume, sell_volume, current_depth, previous_depth]
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('Volumes and depths must be finite and nonnegative')
    total = buy_volume + sell_volume
    return {
        'buy_pressure': buy_volume / total if total else 0.0,
        'sell_pressure': sell_volume / total if total else 0.0,
        'order_imbalance': (buy_volume - sell_volume) / total if total else 0.0,
        'liquidity_depletion': max(0.0, 1 - current_depth / previous_depth) if previous_depth else 0.0,
    }


def synthetic_flow(n=720, seed=7):
    """Invented events, no calendar/historical coverage or empirical calibration."""
    if n < 120:
        raise ValueError('At least 120 synthetic events required')
    rng = np.random.default_rng(seed)
    data = np.zeros((n, 5), dtype=np.float64)
    imbalance, vol, depth = 0.0, 0.002, 10000.0
    for t in range(n):
        imbalance = np.clip(0.8 * imbalance + rng.normal(0, 0.18), -1, 1)
        vol = max(0.0001, 0.88 * vol + 0.0002 + abs(imbalance) * 0.00015 + rng.normal(0, 0.00008))
        old_depth = depth
        depth = max(100.0, 0.8 * depth + 2000 - 400 * abs(imbalance) + rng.normal(0, 90))
        spread = max(0.001, 0.01 + 14 * vol + 0.012 * abs(imbalance) + rng.normal(0, 0.001))
        data[t] = spread, depth, vol, imbalance, max(0.0, 1 - depth / old_depth)
    return data


def prepare_windows(data, lookback=12):
    """Windows never cross split boundaries; first lookback targets per split purged.

    Feature at event t forecasts target at t+1. Later held-out windows may use
    earlier observed held-out features, as in sequential one-step forecasting.
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 2 or data.shape[1] != 5 or not np.isfinite(data).all():
        raise ValueError('Expected finite [events, 5] data')
    if not isinstance(lookback, int) or lookback < 2:
        raise ValueError('lookback must be an integer >= 2')
    edges = (0, int(len(data) * 0.6), int(len(data) * 0.8), len(data))
    if min(np.diff(edges)) <= lookback:
        raise ValueError('Insufficient events for three purged splits')
    mean = data[:edges[1]].mean(axis=0)
    scale = data[:edges[1]].std(axis=0)
    scale = np.maximum(scale, 1e-8)
    z = (data - mean) / scale
    splits = {}
    for name, start, end in zip(('train', 'validation', 'test'), edges[:-1], edges[1:]):
        idx = np.arange(start + lookback, end)
        splits[name] = {
            'x': np.stack([z[i-lookback:i] for i in idx]).astype(np.float32),
            'y': z[idx, :3].astype(np.float32),
            'target_index': idx, 'input_start': idx - lookback, 'input_end': idx - 1,
        }
    return {'splits': splits, 'mean': mean, 'scale': scale, 'edges': edges}


def run_forecast_demo(seed=7, epochs=10, n=720, data=None, model_directory=None):
    """Train three actual neural models; select epochs using validation only."""
    if not isinstance(epochs, int) or epochs < 1:
        raise ValueError('epochs must be positive')
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise RuntimeError('Install requirements-ml.txt in .venv-ml for neural forecasts') from exc

    data = synthetic_flow(n, seed) if data is None else np.asarray(data, dtype=float)
    n = len(data)
    prepared = prepare_windows(data)
    splits = prepared['splits']
    scale = prepared['scale'][:3]
    previous_threads = torch.get_num_threads()
    previous_determinism = torch.are_deterministic_algorithms_enabled()
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)

    class LSTM(nn.Module):
        def __init__(self):
            super().__init__()
            self.sequence = nn.LSTM(5, 16, batch_first=True)
            self.output = nn.Linear(16, 3)

        def forward(self, x):
            return self.output(self.sequence(x)[0][:, -1])

    class Transformer(nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding = nn.Linear(5, 16)
            self.position = nn.Parameter(torch.randn(1, 12, 16) * 0.01)
            layer = nn.TransformerEncoderLayer(16, 2, 32, dropout=0.0, batch_first=True)
            self.sequence = nn.TransformerEncoder(layer, 1, enable_nested_tensor=False)
            self.output = nn.Linear(16, 3)

        def forward(self, x):
            # All tokens precede the prediction; bidirectional historical attention is causal to target.
            return self.output(self.sequence(self.embedding(x) + self.position)[:, -1])

    def mae(prediction, target):
        errors = np.abs(prediction - target).mean(axis=0) * scale
        return dict(zip(TARGETS, map(float, errors)))

    # ARIMA(1,1,0), with intercept in first differences, fitted to training only.
    train_z = (data[:prepared['edges'][1], :3] - prepared['mean'][:3]) / scale
    delta = np.diff(train_z, axis=0)
    coefficients = np.stack([np.linalg.lstsq(
        np.column_stack([np.ones(len(delta)-1), delta[:-1, j]]), delta[1:, j], rcond=None
    )[0] for j in range(3)])

    def arima(x):
        return x[:, -1, :3] + coefficients[:, 0] + coefficients[:, 1] * (x[:, -1, :3] - x[:, -2, :3])

    results, predictions = {}, {}
    try:
        with torch.random.fork_rng(devices=[]):
            for index, (name, cls) in enumerate((('LSTM', LSTM), ('Transformer', Transformer), ('ARIMA-LSTM', LSTM))):
                torch.manual_seed(seed + index)
                model = cls()
                optimizer = torch.optim.Adam(model.parameters(), lr=0.008)
                tensors = {}
                for part, values in splits.items():
                    base = arima(values['x']) if name == 'ARIMA-LSTM' else np.zeros_like(values['y'])
                    tensors[part] = (torch.from_numpy(values['x']), torch.from_numpy((values['y'] - base).astype(np.float32)), base)
                train_x, train_y, _ = tensors['train']
                valid_x, valid_y, _ = tensors['validation']
                best_score, best_epoch, checkpoint = math.inf, 0, None
                for epoch in range(epochs):
                    model.train()
                    optimizer.zero_grad()
                    loss = nn.functional.mse_loss(model(train_x), train_y)
                    loss.backward()
                    nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                    model.eval()
                    with torch.no_grad():
                        score = float(torch.mean(torch.abs(model(valid_x) - valid_y)))
                    if score < best_score:
                        best_score, best_epoch = score, epoch + 1
                        checkpoint = copy.deepcopy(model.state_dict())
                model.load_state_dict(checkpoint)
                if model_directory is not None:
                    from pathlib import Path
                    directory = Path(model_directory)
                    directory.mkdir(parents=True, exist_ok=True)
                    torch.save(checkpoint, directory / (name.lower() + '.pt'))
                test_x, _, base = tensors['test']
                with torch.no_grad():
                    prediction = model(test_x).numpy() + base
                # Physical target floor in raw units; no held-out fitting involved.
                floor = -prepared['mean'][:3] / scale
                prediction = np.maximum(prediction, floor)
                predictions[name] = prediction * scale + prepared['mean'][:3]
                results[name] = {
                    'selected_epoch': best_epoch,
                    'validation_standardized_mae': best_score,
                    'test_mae': mae(prediction, splits['test']['y']),
                    'test_samples': len(test_x),
                }
    finally:
        torch.set_num_threads(previous_threads)
        torch.use_deterministic_algorithms(previous_determinism)
    test = splits['test']
    validation = splits['validation']
    baseline_validation = float(np.abs(validation['x'][:, -1, :3] - validation['y']).mean())
    validation_scores = {'persistence': baseline_validation,
                         **{name: result['validation_standardized_mae'] for name, result in results.items()}}
    selected = min(validation_scores, key=validation_scores.get)
    predictions['persistence'] = test['x'][:, -1, :3] * scale + prepared['mean'][:3]
    return {
        'data_source': 'seeded synthetic events generated locally; no market validation',
        'seed': seed, 'events': n, 'epochs_budget': epochs, 'targets': list(TARGETS),
        'units': {'spread_width': 'currency per ETF share', 'displayed_liquidity': 'shares', 'volatility': 'fraction per synthetic event'},
        'horizon': 'one synthetic event ahead',
        'split_policy': 'chronological 60/20/20; raw windows disjoint; train-only scaler; validation-only epoch selection',
        'split_ranges': {key: {'first_input': int(val['input_start'][0]), 'first_target': int(val['target_index'][0]), 'last_target': int(val['target_index'][-1]), 'samples': len(val['y'])} for key, val in splits.items()},
        'baseline': {'name': 'last observed value', 'test_mae': mae(test['x'][:, -1, :3], test['y'])},
        'models': results,
        'selection': {'model': selected, 'criterion': 'lowest validation standardized MAE, including persistence',
                      'validation_scores': validation_scores},
        'heldout_forecasts': [dict(target_index=int(i), as_of_index=int(i)-1,
                                  **dict(zip(TARGETS, map(float, row))))
                              for i, row in zip(test['target_index'], predictions[selected])],
        'arima_specification': 'ARIMA(1,1,0) with drift, conditional OLS on train only; LSTM forecasts residual correction',
        'training_parameters': {'mean': prepared['mean'].tolist(), 'scale': prepared['scale'].tolist(),
                                'arima_coefficients': coefficients.tolist(), 'torch_version': str(torch.__version__)},
        'limitations': ['Synthetic data only; no evidence of tradable edge.', 'Forecast errors do not imply profitability; costs and fills require separate simulation.', 'No historical crisis calibration, six-month historical test, or elapsed paper trading.'],
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--epochs', type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(run_forecast_demo(args.seed, args.epochs), indent=2, allow_nan=False))
