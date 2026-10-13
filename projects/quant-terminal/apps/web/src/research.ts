import { object, rows, scalar, validDecimalString, type Job, type Json, type JsonObject } from './api'

export interface BacktestInput {
  strategy: string
  snapshotId: string
  instrumentId: string
  start: string
  end: string
  currency: string
  quantity: string
  initialCapital: string
  fee: string
  slippage: string
  participation: string
  seed: string
  fastPeriod: string
  slowPeriod: string
  lookback: string
}

export function backtestRequest(input: BacktestInput): JsonObject {
  for (const [label, value] of Object.entries({
    quantity: input.quantity, initialCapital: input.initialCapital,
    fee: input.fee, slippage: input.slippage, participation: input.participation,
  })) {
    if (!validDecimalString(value)) throw new Error(`${label} must be an exact decimal string`)
  }
  if (!input.snapshotId || !input.instrumentId) throw new Error('Select a dataset')
  if (!input.start || !input.end || input.start >= input.end) throw new Error('Choose a valid date interval')
  if (!Number.isSafeInteger(Number(input.seed))) throw new Error('Seed must be an integer')
  const parameters: JsonObject = { instrument_id: input.instrumentId, quantity: input.quantity }
  if (input.strategy === 'moving_average_cross') {
    parameters.fast_period = integer(input.fastPeriod, 'Fast period')
    parameters.slow_period = integer(input.slowPeriod, 'Slow period')
  } else if (input.strategy === 'simple_momentum') {
    parameters.lookback = integer(input.lookback, 'Lookback')
  } else if (input.strategy !== 'buy_and_hold') throw new Error('Select a supported strategy')
  return {
    strategy: input.strategy,
    parameters,
    snapshot_ids: [input.snapshotId],
    start: `${input.start}T00:00:00Z`,
    end: `${input.end}T00:00:00Z`,
    currency: input.currency,
    initial_capital: input.initialCapital,
    seed: Number(input.seed),
    execution: {
      commission_per_unit: input.fee,
      slippage_kind: 'fixed',
      slippage_value: input.slippage,
      maximum_participation: input.participation,
    },
    analytics: { annualization: 252 },
    benchmark: input.instrumentId,
  }
}

export function integer(value: string, label: string, minimum = 0): number {
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < minimum) throw new Error(`${label} must be an integer ≥ ${minimum}`)
  return Number(value)
}

export function dimensionChoices(input: string, parameter: string): Json[] {
  const choices = input.split(',').map(value => value.trim()).filter(Boolean)
  if (!choices.length) throw new Error('Enter at least one parameter choice')
  if (['fast_period', 'slow_period', 'lookback'].includes(parameter)) return choices.map(value => integer(value, parameter, 1))
  if (choices.some(value => !validDecimalString(value))) throw new Error('Parameter choices must be exact decimal strings')
  return choices
}

export function optimizationRequest(backtest: JsonObject, dimension: string, choices: string, objective: string, budget: string, seed: string, constraintMetric: string, threshold: string, secondDimension = '', secondChoices = '', constraintBound: 'minimum' | 'maximum' = 'maximum'): JsonObject {
  const dimensions: Json[] = [{ name: dimension, choices: dimensionChoices(choices, dimension) }]
  if (secondDimension.trim()) {
    if (secondDimension === dimension) throw new Error('Parameter dimensions must differ')
    dimensions.push({ name: secondDimension, choices: dimensionChoices(secondChoices, secondDimension) })
  }
  const constraints: Json[] = []
  if (constraintMetric.trim()) {
    if (!validDecimalString(threshold)) throw new Error('Constraint threshold must be an exact decimal string')
    constraints.push({ metric: constraintMetric.trim(), bound: constraintBound, threshold })
  }
  return {
    backtest,
    dimensions,
    method: 'grid',
    candidate_budget: integer(budget, 'Candidate budget', 1),
    seed: integer(seed, 'Seed'),
    objective_metric: objective,
    direction: 'maximize',
    constraints,
  }
}

export function runId(result: Json): string | null {
  const id = object(result)?.run_id
  return typeof id === 'string' ? id : null
}

export function jobForRun(runIdentity: string | null, jobs: Job[]): Job | null {
  if (!runIdentity) return null
  return jobs.find(job => runId(job.result) === runIdentity
    || runIdentity === `run-${job.id}`
    || runIdentity.startsWith(`${job.id}-`)) ?? null
}

export function trialRows(result: Json): JsonObject[] { return rows(object(result)?.trials ?? null) }
export function windowRows(result: Json): JsonObject[] { return rows(object(result)?.windows ?? null) }
export function pathRows(result: Json): JsonObject[] { return rows(object(object(result)?.result ?? null)?.paths ?? null) }

export function heatmapData(trials: JsonObject[]): { x: string[]; y: string[]; z: Array<Array<number | null>>; xName: string; yName: string } | null {
  if (!trials.length) return null
  const first = object(trials[0].parameters ?? null)
  const dimensions = first ? Object.keys(first) : []
  if (dimensions.length !== 2) return null
  const [xName, yName] = dimensions
  const x = [...new Set(trials.map(row => scalar(object(row.parameters ?? null)?.[xName])))]
  const y = [...new Set(trials.map(row => scalar(object(row.parameters ?? null)?.[yName])))]
  const z = y.map(yValue => x.map(xValue => {
    const row = trials.find(item => scalar(object(item.parameters ?? null)?.[xName]) === xValue && scalar(object(item.parameters ?? null)?.[yName]) === yValue)
    const score = object(row?.score ?? null)?.objective
    return typeof score === 'string' && Number.isFinite(Number(score)) ? Number(score) : null
  }))
  return { x, y, z, xName, yName }
}
