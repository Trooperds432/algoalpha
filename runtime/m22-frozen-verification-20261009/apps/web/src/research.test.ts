import { describe, expect, it } from 'vitest'
import { backtestRequest, dimensionChoices, heatmapData, jobForRun, optimizationRequest, type BacktestInput } from './research'
import type { Job } from './api'

const input: BacktestInput = {
  strategy: 'buy_and_hold', snapshotId: 'a'.repeat(64), instrumentId: 'ETF-DEMO',
  start: '2026-07-01', end: '2026-09-01', currency: 'USD', quantity: '0.125',
  initialCapital: '10000.0001', fee: '0.01', slippage: '0.005', participation: '0.1',
  seed: '17', fastPeriod: '5', slowPeriod: '20', lookback: '10',
}

describe('research inputs', () => {
  it('builds the exact M13 backtest request', () => {
    const request = backtestRequest(input)
    expect(request.initial_capital).toBe('10000.0001')
    expect(request.parameters).toEqual({ instrument_id: 'ETF-DEMO', quantity: '0.125' })
    expect(request.execution).toEqual({ commission_per_unit: '0.01', slippage_kind: 'fixed', slippage_value: '0.005', maximum_participation: '0.1' })
  })
  it('rejects nonfinite finance values and invalid dates', () => {
    expect(() => backtestRequest({ ...input, fee: 'NaN' })).toThrow('fee')
    expect(() => backtestRequest({ ...input, end: input.start })).toThrow('date interval')
  })
  it('keeps quantity choices exact and period choices numeric', () => {
    expect(dimensionChoices('1.01, 1.02', 'quantity')).toEqual(['1.01', '1.02'])
    expect(dimensionChoices('5, 10', 'lookback')).toEqual([5, 10])
  })
  it('only offers a heatmap for two recorded dimensions', () => {
    expect(heatmapData([{ parameters: { quantity: '1' }, score: { objective: '0.1' } }])).toBeNull()
    expect(heatmapData([{ parameters: { quantity: '1', lookback: 5 }, score: { objective: '0.1' } }])?.z).toEqual([[0.1]])
  })
  it('defines two dimensions and a minimum constraint without converting exact values', () => {
    const request = optimizationRequest(backtestRequest(input), 'quantity', '1.01,2.02', 'sharpe', '4', '17', 'maximum_drawdown', '0.15', 'lookback', '5,10', 'minimum')
    expect(request.dimensions).toEqual([{ name: 'quantity', choices: ['1.01', '2.02'] }, { name: 'lookback', choices: [5, 10] }])
    expect(request.constraints).toEqual([{ metric: 'maximum_drawdown', bound: 'minimum', threshold: '0.15' }])
  })
  it('finds the recorded job for a selected run', () => {
    const job: Job = {
      id: 'abc', kind: 'backtest', status: 'succeeded', request: {},
      result: { run_id: 'run-abc' }, error: null,
      created_at: '2026-09-30T00:00:00Z', updated_at: '2026-09-30T00:00:00Z',
    }
    expect(jobForRun('run-abc', [job])).toBe(job)
    expect(jobForRun('unrelated', [job])).toBeNull()
  })
})
