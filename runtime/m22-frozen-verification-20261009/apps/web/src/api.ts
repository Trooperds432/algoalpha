export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type JsonObject = { [key: string]: Json }

export interface Job {
  id: string
  kind: string
  status: string
  request: JsonObject
  result: Json
  error: Json
  created_at: string
  updated_at: string
}

export interface Dataset {
  snapshot_id: string
  instrument_id: string
  currency: string
  row_count: number
  source: string
  content_hash: string
}

export interface Strategy {
  name: string
  parameters: string[]
}

export interface Table {
  name: string
  columns: string[]
  rows: Json[][]
}

export interface Chart {
  name: string
  title: string
  kind: 'line' | 'bar' | 'scatter'
  x_label: string
  y_label: string
  series: Array<{ name: string; points: Array<[string, string | null]> }>
  note: string | null
}

export interface Report {
  schema_version: string
  strategy_name: string
  currency: string
  start: string
  end: string
  metrics: Array<Metric & { name: string }>
  tables: Table[]
  charts: Chart[]
}

export interface Metric {
  value: string | null
  unit: string
  status: string
  reason: string | null
  observations: number
}

export interface Run {
  run_id: string
  config: JsonObject
  result_fingerprint: string
  metrics: Array<[string, Metric]>
  created_at?: string
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T>(path: string, options?: { method?: 'POST'; body?: JsonObject; signal?: AbortSignal }): Promise<T> {
  const response = await fetch(path, {
    method: options?.method ?? 'GET',
    headers: options?.method === 'POST' ? { 'Content-Type': 'application/json' } : undefined,
    body: options?.method === 'POST' ? JSON.stringify(options.body ?? {}) : undefined,
    signal: options?.signal,
    credentials: 'same-origin',
  })
  const raw = await response.text()
  let data: unknown
  try { data = raw ? JSON.parse(raw) : null } catch { throw new ApiError(response.status, `Invalid API response from ${path}`) }
  if (!response.ok) {
    const message = typeof data === 'object' && data !== null && 'message' in data
      ? String(data.message) : typeof data === 'object' && data !== null && 'error' in data
        ? String(data.error) : `Request failed (${response.status})`
    throw new ApiError(response.status, message)
  }
  return data as T
}

export const submit = (path: string, request: JsonObject) => api<Job>(path, { method: 'POST', body: request })

export function object(value: Json): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null
}

export function rows(value: Json): JsonObject[] {
  return Array.isArray(value) ? value.map(object).filter((row): row is JsonObject => row !== null) : []
}

export function scalar(value: Json | undefined): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function validDecimalString(value: string): boolean {
  return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim())
}

export function parseRequest(text: string): JsonObject {
  const value: unknown = JSON.parse(text)
  if (value === null || Array.isArray(value) || typeof value !== 'object') throw new Error('Request must be a JSON object')
  return value as JsonObject
}

export function metricMap(items: Array<[string, Metric]> | Array<Metric & { name: string }> | undefined): Record<string, Metric> {
  if (!Array.isArray(items)) return {}
  return Object.fromEntries(items.map(item => Array.isArray(item) ? item : [item.name, item]))
}

export function chartByName(report: Report | null, name: string): Chart | null {
  return report?.charts.find(chart => chart.name === name) ?? null
}

export function displayMetric(metric?: Metric): string {
  if (!metric || metric.value === null) return 'Unavailable'
  const value = Number(metric.value)
  if (!Number.isFinite(value)) return metric.value
  if (Math.abs(value) >= 1e12 || (value !== 0 && Math.abs(value) < 1e-7)) return value.toExponential(3)
  if (metric.unit === 'fraction' || metric.unit === 'loss_fraction' || metric.unit.startsWith('fraction_')) {
    const percent = value * 100
    const digits = percent !== 0 && Math.abs(percent) < 0.01 ? 6 : 2
    return `${new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits }).format(percent)}%`
  }
  return new Intl.NumberFormat('en-GB', { maximumFractionDigits: metric.unit === 'ratio' ? 4 : 2 }).format(value)
}

export function tableByName(report: Report | null, name: string): Table | null {
  return report?.tables.find(table => table.name === name) ?? null
}
