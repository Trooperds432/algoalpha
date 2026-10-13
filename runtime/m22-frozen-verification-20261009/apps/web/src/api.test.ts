import { describe, expect, it } from 'vitest'
import { displayMetric, metricMap, parseRequest, validDecimalString } from './api'

describe('research request and result boundary', () => {
  it('keeps exact money and quantity strings in JSON', () => {
    const request = parseRequest('{"initial_capital":"10000.0001","parameters":{"quantity":"0.125"}}')
    expect(request.initial_capital).toBe('10000.0001')
    expect(request.parameters).toEqual({ quantity: '0.125' })
  })
  it('requires a request object', () => {
    expect(() => parseRequest('[1,2]')).toThrow('Request must be a JSON object')
  })
  it('rejects nonfinite decimal text before constructing a request', () => {
    expect(validDecimalString('NaN')).toBe(false)
    expect(validDecimalString('Infinity')).toBe(false)
    expect(validDecimalString('0.005')).toBe(true)
  })
  it('preserves undefined metric states', () => {
    expect(metricMap([['sharpe', { value: null, unit: 'ratio', status: 'undefined', reason: 'zero volatility', observations: 2 }]]).sharpe.value).toBeNull()
  })
  it('formats visible metrics without changing the source value', () => {
    const metric = { value: '0.1234567890123456789', unit: 'fraction', status: 'ok', reason: null, observations: 20 }
    expect(displayMetric(metric)).toBe('12.35%')
    expect(metric.value).toBe('0.1234567890123456789')
  })
  it('shows nonzero risk loss fractions as percentages', () => {
    expect(displayMetric({ value: '0.00456', unit: 'loss_fraction', status: 'insufficient_sample', reason: 'tail sample too small', observations: 10 })).toBe('0.46%')
    expect(displayMetric({ value: '0.0000001', unit: 'loss_fraction', status: 'ok', reason: null, observations: 10 })).toBe('0.00001%')
  })
})
