import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, chartByName, displayMetric, metricMap, object, rows, scalar, submit, tableByName, type Dataset, type Job, type Json, type JsonObject, type Metric, type Report, type Run, type Strategy, type Table } from './api'
import { Plot, ReportChart } from './Plot'
import { Paper } from './Paper'
import { PortfolioResearch } from './PortfolioResearch'
import { AdvancedAssets } from './AdvancedAssets'
import { backtestRequest, heatmapData, integer, jobForRun, optimizationRequest, pathRows, runId, trialRows, windowRows, type BacktestInput } from './research'

type Workspace = 'Dashboard' | 'Strategies' | 'Data' | 'Backtests' | 'Optimisation' | 'Walk Forward' | 'Monte Carlo' | 'Experiments' | 'Paper Trading' | 'Broker' | 'Portfolio Research' | 'Advanced Assets'
const workspaces: Workspace[] = ['Dashboard', 'Strategies', 'Data', 'Backtests', 'Optimisation', 'Walk Forward', 'Monte Carlo', 'Experiments', 'Paper Trading', 'Broker', 'Portfolio Research', 'Advanced Assets']
const sections = ['Overview', 'Trades', 'Orders', 'Fills', 'Positions', 'Drawdowns', 'Monthly Returns', 'Execution', 'Logs'] as const
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])
const operations: Record<string, string> = { backtest: '/backtests', optimize: '/optimisations', 'walk-forward': '/walk-forwards', 'monte-carlo': '/monte-carlo', robustness: '/robustness' }

const initialForm: BacktestInput = {
  strategy: 'buy_and_hold', snapshotId: '', instrumentId: '', start: '2026-07-01', end: '2026-09-01',
  currency: 'USD', quantity: '10', initialCapital: '10000', fee: '0.1', slippage: '0.01',
  participation: '0.01', seed: '20260927', fastPeriod: '5', slowPeriod: '20', lookback: '10',
}

function Label({ children }: { children: React.ReactNode }) { return <span className="field-label">{children}</span> }
function Field({ label, value, onChange, type = 'text', hint }: { label: string; value: string; onChange: (value: string) => void; type?: string; hint?: string }) {
  return <label className="field"><Label>{label}</Label><input type={type} value={value} onChange={event => onChange(event.target.value)} />{hint && <small>{hint}</small>}</label>
}
function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return <label className="field"><Label>{label}</Label><select value={value} onChange={event => onChange(event.target.value)}>{options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
}
function MetricCard({ name, metric }: { name: string; metric?: Metric }) {
  return <div className="metric"><span>{name.replaceAll('_', ' ')}</span><strong title={metric?.value ?? metric?.reason ?? 'Unavailable'}>{displayMetric(metric)}</strong><small>{metric?.value === null ? metric.reason ?? metric.status : metric?.unit ?? 'No run selected'}</small></div>
}
function DataTable({ table, limit = 150 }: { table: Table | null; limit?: number }) {
  if (!table) return <div className="empty">No recorded table for this view.</div>
  return <div className="table-wrap"><table><thead><tr>{table.columns.map(column => <th key={column}>{column.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{table.rows.slice(0, limit).map((row, index) => <tr key={index}>{row.map((cell, column) => <td key={column} title={scalar(cell)}>{scalar(cell)}</td>)}</tr>)}</tbody></table>{table.rows.length > limit && <p className="note">Showing {limit} of {table.rows.length} rows. Download the verified report for the full table.</p>}</div>
}
function JobStrip({ job, onCancel, onOpenRun }: { job: Job | null; onCancel: (id: string) => void; onOpenRun: (id: string) => void }) {
  if (!job) return <div className="empty subtle">No operation submitted in this session.</div>
  const id = runId(job.result)
  return <div className="job-strip"><div><strong>{job.kind.replaceAll('-', ' ')}</strong><span className={`status ${job.status}`}>{job.status}</span><div className="mono muted">Job {job.id}</div></div><div className="actions">{id && <button onClick={() => onOpenRun(id)}>Open run</button>}{!terminal.has(job.status) && <button className="danger" onClick={() => onCancel(job.id)}>Cancel</button>}</div>{job.error && <div className="error">{scalar(job.error)}</div>}</div>
}

interface JournalEntry { sequence: number; event: JsonObject }
function Journal({ job }: { job: Job | null }) {
  const [events, setEvents] = useState<JournalEntry[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    if (!job) { setEvents([]); return }
    let active = true
    const load = () => { void api<JournalEntry[]>(`/jobs/${encodeURIComponent(job.id)}/events?after=0&limit=100`).then(data => { if (active) { setEvents(data); setError('') } }).catch(reason => { if (active) setError(String(reason)) }) }
    load()
    const timer = window.setInterval(load, terminal.has(job.status) ? 10000 : 2000)
    return () => { active = false; window.clearInterval(timer) }
  }, [job?.id, job?.status])
  return <div className="journal"><h3>Job journal <span className="muted">{job ? `${events.length} recorded events` : 'No associated job'}</span></h3>{error && <div className="error">{error}</div>}{!events.length ? <p className="note">{job ? 'No worker events recorded yet.' : 'No associated job found in the recent job list.'}</p> : <div className="journal-list">{events.slice(-12).reverse().map(row => <div key={row.sequence}><span>#{row.sequence}</span><strong>{scalar(row.event.kind ?? row.event.event)}</strong><span>{row.event.index !== undefined ? `Index ${scalar(row.event.index)}` : ''}</span><code title={scalar(row.event.value)}>{scalar(row.event.value).slice(0, 160)}</code></div>)}</div>}</div>
}

export function App() {
  const [workspace, setWorkspace] = useState<Workspace>('Dashboard')
  const [health, setHealth] = useState<{ status: string; broker_orders_enabled: boolean } | null>(null)
  const [customPythonAvailable, setCustomPythonAvailable] = useState(false)
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [form, setForm] = useState<BacktestInput>(initialForm)
  const [notice, setNotice] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [section, setSection] = useState<(typeof sections)[number]>('Overview')

  const refresh = useCallback(async () => {
    const outcomes = await Promise.allSettled([
      api<{ status: string; broker_orders_enabled: boolean }>('/health'),
      api<Strategy[]>('/strategies'), api<Dataset[]>('/datasets'), api<Run[]>('/runs'), api<Job[]>('/jobs'),
      api<{ isolated_python: { available: boolean } }>('/sandbox/capabilities'),
    ])
    if (outcomes[0].status === 'fulfilled') setHealth(outcomes[0].value)
    else setHealth(null)
    if (outcomes[1].status === 'fulfilled') setStrategies(outcomes[1].value)
    if (outcomes[2].status === 'fulfilled') setDatasets(outcomes[2].value)
    if (outcomes[3].status === 'fulfilled') setRuns(outcomes[3].value)
    if (outcomes[4].status === 'fulfilled') setJobs(outcomes[4].value)
    setCustomPythonAvailable(outcomes[5].status === 'fulfilled' && outcomes[5].value.isolated_python.available)
    if (outcomes[0].status === 'rejected') setNotice(`Local API unavailable: ${String(outcomes[0].reason)}`)
    setLoading(false)
  }, [])

  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => clearInterval(timer) }, [refresh])
  useEffect(() => {
    if (!selectedJobId) return
    const timer = window.setInterval(() => { void api<Job>(`/jobs/${encodeURIComponent(selectedJobId)}`).then(job => {
      setJobs(current => [job, ...current.filter(row => row.id !== job.id)])
      if (job.status === 'succeeded' && job.kind === 'backtest') { const id = runId(job.result); if (id) setSelectedRunId(id) }
    }).catch(error => setNotice(String(error))) }, 1500)
    return () => clearInterval(timer)
  }, [selectedJobId])
  useEffect(() => {
    if (!selectedRunId) { setReport(null); return }
    let active = true
    setReport(null)
    void api<Report>(`/runs/${encodeURIComponent(selectedRunId)}/report`).then(value => { if (active) setReport(value) }).catch(error => { if (active) setNotice(`Report unavailable: ${String(error)}`) })
    return () => { active = false }
  }, [selectedRunId])
  useEffect(() => {
    if (!datasets.length || form.snapshotId) return
    setForm(current => ({ ...current, snapshotId: datasets[0].snapshot_id, instrumentId: datasets[0].instrument_id, currency: datasets[0].currency }))
  }, [datasets, form.snapshotId])

  const activeJob = jobs.find(job => job.id === selectedJobId) ?? null
  const selectedRun = runs.find(run => run.run_id === selectedRunId) ?? null
  const selectedRunJob = jobForRun(selectedRunId, jobs)
  const setInput = (key: keyof BacktestInput, value: string) => setForm(current => ({ ...current, [key]: value }))
  const selectDataset = (id: string) => {
    const dataset = datasets.find(item => item.snapshot_id === id)
    if (dataset) setForm(current => ({ ...current, snapshotId: id, instrumentId: dataset.instrument_id, currency: dataset.currency }))
  }
  const submitOperation = async (kind: string, request: JsonObject) => {
    setNotice('')
    try {
      const job = await submit(operations[kind], request)
      setJobs(current => [job, ...current.filter(item => item.id !== job.id)])
      setSelectedJobId(job.id)
      setNotice(`Queued ${kind.replaceAll('-', ' ')} job ${job.id}. Research output is not an approval.`)
    } catch (error) { setNotice(String(error)) }
  }
  const cancel = async (id: string) => {
    try { const job = await api<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: {} }); setJobs(current => [job, ...current.filter(row => row.id !== id)]) }
    catch (error) { setNotice(String(error)) }
  }
  const openRun = (id: string) => { setSelectedRunId(id); setWorkspace('Backtests'); setSection('Overview') }

  return <div className="shell">
    <aside className="sidebar"><div className="brand"><span className="mark">A</span><div><strong>AlgoAlpha</strong><small>RESEARCH TERMINAL</small></div></div><nav aria-label="Workspaces">{workspaces.map(name => <button key={name} className={workspace === name ? 'active' : ''} onClick={() => setWorkspace(name)}>{name}</button>)}</nav><div className="sidebar-foot"><span className="status-dot" /> Local research only<br /><small>Broker orders disabled</small></div></aside>
    <div className="main"><header className="topbar"><div><span className="eyebrow">RESEARCH / {workspace.toUpperCase()}</span><h1>{workspace}</h1></div><div className="top-actions"><span className={`connection ${health?.status === 'ok' ? 'good' : 'bad'}`}>{health?.status === 'ok' ? 'API connected' : 'API unavailable'}</span><button onClick={() => void refresh()}>Refresh</button></div></header>
    {notice && <div className="notice" role="status"><span>{notice}</span><button aria-label="Dismiss notice" onClick={() => setNotice('')}>×</button></div>}
    <main>{loading ? <div className="empty">Loading terminal…</div> : <>
      {workspace === 'Dashboard' && <Dashboard health={health} jobs={jobs} runs={runs} datasets={datasets} onOpenRun={openRun} onWorkspace={setWorkspace} />}
      {workspace === 'Strategies' && <><p className="note" role="status">{customPythonAvailable ? 'Custom Python capability available.' : 'Custom Python submissions are disabled. Choose a trusted built-in strategy.'}</p><Strategies strategies={strategies} onUse={name => { setInput('strategy', name); setWorkspace('Backtests') }} /></>}
      {workspace === 'Data' && <Data datasets={datasets} onImported={() => void refresh()} onNotice={setNotice} />}
      {workspace === 'Backtests' && <div className="backtest-grid"><div className="panel config-panel"><div className="panel-head"><h2>Backtest setup</h2><span className="tag">Trusted strategies</span></div><BacktestForm form={form} datasets={datasets} strategies={strategies} setInput={setInput} selectDataset={selectDataset} onRun={() => { try { void submitOperation('backtest', backtestRequest(form)) } catch (error) { setNotice(String(error)) } }} /><JobStrip job={activeJob?.kind === 'backtest' ? activeJob : null} onCancel={cancel} onOpenRun={openRun} /></div><div className="analysis-column"><RunPicker runs={runs} selected={selectedRunId} onSelect={setSelectedRunId} /><div className="panel"><div className="panel-head"><h2>Equity & performance</h2>{selectedRunId && <a href={`/runs/${encodeURIComponent(selectedRunId)}/report.html`} target="_blank" rel="noreferrer">Open full report ↗</a>}</div><ReportChart chart={chartByName(report, 'equity')} height={350} />{report && <div className="chart-pair"><ReportChart chart={chartByName(report, 'underwater')} height={250} /><ReportChart chart={chartByName(report, 'benchmark')} height={250} /></div>}</div></div><div className="panel stats-panel"><div className="panel-head"><h2>Summary statistics</h2></div><Summary report={report} /><p className="note">Metrics and assumptions come from the verified report. Unavailable values remain unavailable.</p></div><div className="panel full-span"><div className="tabs" role="tablist">{sections.map(name => <button key={name} role="tab" aria-selected={section === name} className={section === name ? 'selected' : ''} onClick={() => setSection(name)}>{name}</button>)}</div><ReportSection report={report} section={section} job={selectedRunJob} run={selectedRun} /></div></div>}
      {workspace === 'Optimisation' && <Optimization form={form} jobs={jobs} selectedJobId={selectedJobId} submitOperation={submitOperation} onNotice={setNotice} onCancel={cancel} onOpenRun={openRun} />}
      {workspace === 'Walk Forward' && <WalkForward form={form} jobs={jobs} selectedJobId={selectedJobId} submitOperation={submitOperation} onNotice={setNotice} onCancel={cancel} onOpenRun={openRun} />}
      {workspace === 'Monte Carlo' && <MonteCarlo runs={runs} jobs={jobs} selectedJobId={selectedJobId} submitOperation={submitOperation} onNotice={setNotice} onCancel={cancel} onOpenRun={openRun} />}
      {workspace === 'Experiments' && <Experiments runs={runs} jobs={jobs} onOpenRun={openRun} onNotice={setNotice} />}
      {workspace === 'Paper Trading' && <Paper />}
      {workspace === 'Broker' && <Broker />}
      {workspace === 'Portfolio Research' && <PortfolioResearch />}
      {workspace === 'Advanced Assets' && <AdvancedAssets />}
    </>}</main></div>
  </div>
}

function Broker() {
  const [status, setStatus] = useState<JsonObject | null>(null)
  useEffect(() => { void api<JsonObject>('/broker/status').then(setStatus).catch(error => setStatus({ reason: String(error) })) }, [])
  return <div className="panel"><div className="panel-head"><h2>Broker architecture</h2><span className="tag">{scalar(status?.mode)}</span></div>
    <p className="note">Trading 212 Demo adapter architecture. External connection: disabled. External order submission: disabled. Live mode: unavailable.</p>
    <div className="kpis"><div><span>Provider</span><strong>{scalar(status?.provider)}</strong></div><div><span>Demo connection</span><strong>{status?.connection_enabled === true ? 'Enabled' : 'Disabled'}</strong></div><div><span>External orders</span><strong>{status?.order_submission_enabled === true ? 'Enabled' : 'Disabled'}</strong></div><div><span>Live mode</span><strong>{status?.live_mode_available === true ? 'Available' : 'Unavailable'}</strong></div></div>
    <p className="note">{scalar(status?.reason)}. Use Paper Trading for virtual market observations and simulated fills. Broker credentials are never accepted in strategy configuration.</p>
    <button disabled aria-disabled="true">Connect (disabled)</button><button className="danger" disabled aria-disabled="true">Submit orders (disabled)</button>
  </div>
}

function Dashboard({ health, jobs, runs, datasets, onOpenRun, onWorkspace }: { health: { status: string; broker_orders_enabled: boolean } | null; jobs: Job[]; runs: Run[]; datasets: Dataset[]; onOpenRun: (id: string) => void; onWorkspace: (name: Workspace) => void }) {
  return <><div className="hero"><div><span className="eyebrow">LOCAL QUANTITATIVE RESEARCH</span><h2>Every result has a trail.</h2><p>Configure, run, inspect and compare reproducible experiments. The terminal displays recorded evidence; it does not promote a strategy.</p><div className="actions"><button className="primary" onClick={() => onWorkspace('Backtests')}>New backtest</button><button onClick={() => onWorkspace('Experiments')}>Browse experiments</button></div></div><div className="hero-badge">RESEARCH<br />MODE</div></div><div className="kpis"><div><span>Scheduler</span><strong>{health?.status ?? 'Unavailable'}</strong></div><div><span>Datasets</span><strong>{datasets.length}</strong></div><div><span>Verified runs</span><strong>{runs.length}</strong></div><div><span>Jobs</span><strong>{jobs.length}</strong></div></div><div className="two-columns"><div className="panel"><div className="panel-head"><h2>Recent jobs</h2></div><JobList jobs={jobs.slice(0, 8)} onOpenRun={onOpenRun} /></div><div className="panel"><div className="panel-head"><h2>Recent experiments</h2></div><RunList runs={runs.slice(0, 8)} onOpenRun={onOpenRun} /></div></div></>
}
function Strategies({ strategies, onUse }: { strategies: Strategy[]; onUse: (name: string) => void }) {
  return <div className="card-grid">{strategies.map(strategy => <div className="panel strategy-card" key={strategy.name}><span className="tag">BUILT-IN · TRUSTED</span><h2>{strategy.name.replaceAll('_', ' ')}</h2><p>Parameters: {strategy.parameters.join(', ')}</p><button className="primary" onClick={() => onUse(strategy.name)}>Configure backtest</button></div>)}{!strategies.length && <div className="empty">No strategies returned by the local API.</div>}</div>
}
function Data({ datasets, onImported, onNotice }: { datasets: Dataset[]; onImported: () => void; onNotice: (message: string) => void }) {
  const [csv, setCsv] = useState('')
  const [source, setSource] = useState('local-research-import')
  const [instrument, setInstrument] = useState('ETF-DEMO')
  const [symbol, setSymbol] = useState('SYNTHETIC')
  const [currency, setCurrency] = useState('USD')
  const [asset, setAsset] = useState('etf')
  const [calendar, setCalendar] = useState('XNYS')
  const [timezone, setTimezone] = useState('America/New_York')
  const [tick, setTick] = useState('0.01')
  const [step, setStep] = useState('1')
  const [delay, setDelay] = useState('0')
  const [busy, setBusy] = useState(false)
  const importCsv = async () => {
    try {
      if (!csv.trim()) throw new Error('Choose or paste a CSV file')
      setBusy(true)
      const result = await api<JsonObject>('/datasets', { method: 'POST', body: { csv, source, publication_delay_seconds: integer(delay, 'Publication delay'), instrument: { instrument_id: instrument, symbol, currency, asset_class: asset, calendar, timezone, tick_size: tick, quantity_step: step } } })
      onNotice(`Imported dataset ${scalar(result.snapshot_id)}. Review source and point-in-time assumptions before research.`)
      onImported()
    } catch (error) { onNotice(String(error)) } finally { setBusy(false) }
  }
  return <div className="two-columns"><div className="panel"><div className="panel-head"><h2>Stored snapshots</h2><span className="tag">POINT IN TIME</span></div><div className="dataset-list">{datasets.map(item => <div className="dataset" key={item.snapshot_id}><div><strong>{item.instrument_id}</strong><span className="muted">{item.currency} · {item.row_count} bars</span></div><div className="mono">{item.snapshot_id}</div><small>Source: {item.source}{/synthetic|demo/i.test(item.source) && <span className="synthetic">SYNTHETIC</span>}</small></div>)}{!datasets.length && <div className="empty">No snapshots imported.</div>}</div></div><div className="panel"><div className="panel-head"><h2>Import CSV</h2></div><p className="note">Supply source and instrument metadata explicitly. The server validates market bars and preserves the snapshot hash.</p><div className="form-grid"><Field label="Instrument ID" value={instrument} onChange={setInstrument} /><Field label="Symbol" value={symbol} onChange={setSymbol} /><Field label="Currency" value={currency} onChange={setCurrency} /><Field label="Asset class" value={asset} onChange={setAsset} /><Field label="Calendar" value={calendar} onChange={setCalendar} /><Field label="Timezone" value={timezone} onChange={setTimezone} /><Field label="Tick size" value={tick} onChange={setTick} /><Field label="Quantity step" value={step} onChange={setStep} /><Field label="Source" value={source} onChange={setSource} /><Field label="Publication delay (seconds)" value={delay} onChange={setDelay} /></div><label className="field"><Label>CSV file</Label><input type="file" accept=".csv,text/csv" onChange={event => { const file = event.target.files?.[0]; if (file) void file.text().then(setCsv).catch(error => onNotice(String(error))) }} /></label><label className="field"><Label>CSV preview / paste</Label><textarea rows={8} value={csv} onChange={event => setCsv(event.target.value)} placeholder="instrument_id,frequency,start,end,available_at,open,high,low,close,volume,source" /></label><button className="primary" disabled={busy} onClick={() => void importCsv()}>{busy ? 'Importing…' : 'Import validated CSV'}</button></div></div>
}

function BacktestForm({ form, datasets, strategies, setInput, selectDataset, onRun }: { form: BacktestInput; datasets: Dataset[]; strategies: Strategy[]; setInput: (key: keyof BacktestInput, value: string) => void; selectDataset: (id: string) => void; onRun: () => void }) {
  return <div className="form-stack"><Select label="Strategy" value={form.strategy} onChange={value => setInput('strategy', value)} options={strategies.map(item => ({ value: item.name, label: item.name.replaceAll('_', ' ') }))} /><Select label="Dataset snapshot" value={form.snapshotId} onChange={selectDataset} options={[{ value: '', label: 'Choose a dataset' }, ...datasets.map(item => ({ value: item.snapshot_id, label: `${item.instrument_id} · ${item.row_count} bars` }))]} /><div className="form-grid date-grid"><Field label="Start (UTC)" type="date" value={form.start} onChange={value => setInput('start', value)} /><Field label="End (exclusive UTC)" type="date" value={form.end} onChange={value => setInput('end', value)} /></div><div className="form-grid"><Field label="Quantity" value={form.quantity} onChange={value => setInput('quantity', value)} /><Field label="Initial capital" value={form.initialCapital} onChange={value => setInput('initialCapital', value)} /></div>{form.strategy === 'moving_average_cross' && <div className="form-grid"><Field label="Fast period" value={form.fastPeriod} onChange={value => setInput('fastPeriod', value)} /><Field label="Slow period" value={form.slowPeriod} onChange={value => setInput('slowPeriod', value)} /></div>}{form.strategy === 'simple_momentum' && <Field label="Lookback" value={form.lookback} onChange={value => setInput('lookback', value)} />}<div className="form-grid"><Field label="Fee / unit" value={form.fee} onChange={value => setInput('fee', value)} /><Field label="Fixed slippage" value={form.slippage} onChange={value => setInput('slippage', value)} /><Field label="Max participation" value={form.participation} onChange={value => setInput('participation', value)} /><Field label="Seed" value={form.seed} onChange={value => setInput('seed', value)} /></div><button className="primary wide" disabled={!datasets.length} onClick={onRun}>Run historical backtest</button><p className="note">Research simulation only. No broker order is submitted.</p></div>
}
function RunPicker({ runs, selected, onSelect }: { runs: Run[]; selected: string | null; onSelect: (id: string | null) => void }) {
  return <div className="run-picker"><label>Verified run<select value={selected ?? ''} onChange={event => onSelect(event.target.value || null)}><option value="">Select a run</option>{runs.map(run => <option key={run.run_id} value={run.run_id}>{run.run_id}</option>)}</select></label></div>
}
function Summary({ report }: { report: Report | null }) {
  const metrics = metricMap(report?.metrics)
  return <div className="metric-list">{['total_return', 'cagr', 'sharpe', 'maximum_drawdown', 'annualized_volatility', 'trade_count', 'total_cost', 'turnover'].map(name => <MetricCard key={name} name={name} metric={metrics[name]} />)}</div>
}
function ReportSection({ report, section, job, run }: { report: Report | null; section: (typeof sections)[number]; job: Job | null; run: Run | null }) {
  if (!report) return <div className="empty">Select a verified run to inspect its report.</div>
  if (section === 'Overview') return <><div className="overview-metrics"><Summary report={report} /></div><div className="info-grid"><div><h3>Assumptions</h3><pre>{JSON.stringify((report as unknown as JsonObject).assumptions, null, 2)}</pre></div><div><h3>Provenance</h3><pre>{JSON.stringify({ run_id: run?.run_id, result_fingerprint: run?.result_fingerprint, report_version: report.schema_version, start: report.start, end: report.end }, null, 2)}</pre></div></div></>
  if (section === 'Logs') return <><Journal job={job} /><pre className="code-block">{JSON.stringify({ job_id: job?.id, job_status: job?.status, job_error: job?.error, report_metadata: (report as unknown as JsonObject).metadata }, null, 2)}</pre></>
  if (section === 'Monthly Returns') return <ReportChart chart={chartByName(report, 'monthly_returns')} />
  const names: Record<string, string> = { Trades: 'trades', Orders: 'orders', Fills: 'fills', Positions: 'positions', Drawdowns: 'drawdowns', Execution: 'execution_costs' }
  return <DataTable table={tableByName(report, names[section])} />
}
function JobList({ jobs, onOpenRun }: { jobs: Job[]; onOpenRun: (id: string) => void }) {
  if (!jobs.length) return <div className="empty">No jobs recorded.</div>
  return <div className="list">{jobs.map(job => <div className="list-row" key={job.id}><div><strong>{job.kind.replaceAll('-', ' ')}</strong><small className="mono">{job.id}</small></div><div><span className={`status ${job.status}`}>{job.status}</span>{runId(job.result) && <button className="link-button" onClick={() => onOpenRun(runId(job.result)!)}>Open run</button>}</div></div>)}</div>
}
function RunList({ runs, onOpenRun }: { runs: Run[]; onOpenRun: (id: string) => void }) {
  if (!runs.length) return <div className="empty">No verified runs recorded.</div>
  return <div className="list">{runs.map(run => <div className="list-row" key={run.run_id}><div><strong>{run.run_id}</strong><small className="mono">{run.result_fingerprint?.slice(0, 18)}…</small></div><button onClick={() => onOpenRun(run.run_id)}>Inspect</button></div>)}</div>
}

type SubmitOperation = (kind: string, request: JsonObject) => Promise<void>
type CampaignProps = { form: BacktestInput; jobs: Job[]; selectedJobId: string | null; submitOperation: SubmitOperation; onNotice: (value: string) => void; onCancel: (id: string) => void; onOpenRun: (id: string) => void }

function CampaignJob({ kind, jobs, selectedJobId, onCancel, onOpenRun, children }: { kind: string; jobs: Job[]; selectedJobId: string | null; onCancel: (id: string) => void; onOpenRun: (id: string) => void; children: (job: Job | null) => React.ReactNode }) {
  const [chosen, setChosen] = useState('')
  const matching = jobs.filter(job => job.kind === kind)
  const current = matching.find(job => job.id === chosen) ?? matching.find(job => job.id === selectedJobId) ?? matching[0] ?? null
  return <div className="campaign-output"><div className="panel-head"><h2>Recorded result</h2><select aria-label="Select research job" value={current?.id ?? ''} onChange={event => setChosen(event.target.value)}><option value="">Select job</option>{matching.map(job => <option key={job.id} value={job.id}>{job.id.slice(0, 12)} · {job.status}</option>)}</select></div><JobStrip job={current} onCancel={onCancel} onOpenRun={onOpenRun} /><Journal job={current} />{current && children(current)}</div>
}

function OptimizationFields({ dimension, setDimension, choices, setChoices, second, setSecond, secondChoices, setSecondChoices, objective, setObjective, budget, setBudget, seed, setSeed, constraint, setConstraint, threshold, setThreshold, bound, setBound, available }: {
  dimension: string; setDimension: (value: string) => void; choices: string; setChoices: (value: string) => void;
  second: string; setSecond: (value: string) => void; secondChoices: string; setSecondChoices: (value: string) => void;
  objective: string; setObjective: (value: string) => void; budget: string; setBudget: (value: string) => void;
  seed: string; setSeed: (value: string) => void; constraint: string; setConstraint: (value: string) => void;
  threshold: string; setThreshold: (value: string) => void; bound: 'minimum' | 'maximum'; setBound: (value: 'minimum' | 'maximum') => void;
  available: string[]
}) {
  return <div className="form-stack"><h3>Search space</h3><Select label="Parameter 1" value={dimension} onChange={setDimension} options={available.map(value => ({ value, label: value }))} /><Field label="Choices 1 (comma separated)" value={choices} onChange={setChoices} /><Select label="Parameter 2 · optional" value={second} onChange={setSecond} options={[{ value: '', label: 'None' }, ...available.filter(value => value !== dimension).map(value => ({ value, label: value }))]} />{second && <Field label="Choices 2 (comma separated)" value={secondChoices} onChange={setSecondChoices} />}<div className="form-grid"><Field label="Objective metric" value={objective} onChange={setObjective} /><Field label="Candidate budget" value={budget} onChange={setBudget} /><Field label="Seed" value={seed} onChange={setSeed} /></div><h3>Optional constraint</h3><div className="form-grid"><Field label="Metric" value={constraint} onChange={setConstraint} /><Select label="Bound" value={bound} onChange={value => setBound(value as 'minimum' | 'maximum')} options={[{ value: 'maximum', label: 'Maximum' }, { value: 'minimum', label: 'Minimum' }]} /><Field label="Threshold" value={threshold} onChange={setThreshold} /></div><p className="note">Eligible trials are a research filter. No candidate is selected for deployment.</p></div>
}

function useOptimizationFields(form: BacktestInput) {
  const available = form.strategy === 'moving_average_cross' ? ['quantity', 'fast_period', 'slow_period'] : form.strategy === 'simple_momentum' ? ['quantity', 'lookback'] : ['quantity']
  const [dimension, setDimension] = useState('quantity')
  const [choices, setChoices] = useState('5, 10, 20')
  const [second, setSecond] = useState('')
  const [secondChoices, setSecondChoices] = useState('5, 10')
  const [objective, setObjective] = useState('total_return')
  const [budget, setBudget] = useState('3')
  const [seed, setSeed] = useState('20260927')
  const [constraint, setConstraint] = useState('')
  const [threshold, setThreshold] = useState('0.15')
  const [bound, setBound] = useState<'minimum' | 'maximum'>('maximum')
  return { available, dimension, setDimension, choices, setChoices, second, setSecond, secondChoices, setSecondChoices, objective, setObjective, budget, setBudget, seed, setSeed, constraint, setConstraint, threshold, setThreshold, bound, setBound }
}

function TrialResults({ result, onOpenRun }: { result: Json; onOpenRun: (id: string) => void }) {
  const trials = trialRows(result)
  const heatmap = heatmapData(trials)
  const first = object(trials[0]?.parameters ?? null)
  const oneDimension = first && Object.keys(first).length === 1 ? Object.keys(first)[0] : null
  if (!trials.length) return <div className="empty">Trials will appear after the job succeeds.</div>
  return <><div className="kpis slim"><div><span>Trials recorded</span><strong>{trials.length}</strong></div><div><span>Eligible</span><strong>{trials.filter(trial => trial.status === 'eligible').length}</strong></div><div><span>Completed</span><strong>{scalar(object(result)?.completed)}</strong></div></div>{oneDimension && <Plot title="Objective response" xTitle={oneDimension} yTitle="Recorded objective" traces={[{ type: 'scatter', mode: 'markers+lines', x: trials.map(trial => scalar(object(trial.parameters ?? null)?.[oneDimension])), y: trials.map(trial => { const value = object(trial.score ?? null)?.objective; return typeof value === 'string' ? Number(value) : null }), text: trials.map(trial => scalar(trial.status)) }]} />}{heatmap && <Plot title="Recorded objective surface" xTitle={heatmap.xName} yTitle={heatmap.yName} traces={[{ type: 'heatmap', x: heatmap.x, y: heatmap.y, z: heatmap.z, colorscale: 'Viridis', hoverongaps: false }]} />}<div className="table-wrap"><table><thead><tr><th>Trial</th><th>Parameters</th><th>Status</th><th>Objective</th><th>Run</th><th>Reason</th></tr></thead><tbody>{trials.map(trial => <tr key={scalar(trial.index)}><td>{scalar(trial.index)}</td><td className="mono">{scalar(trial.parameters)}</td><td><span className={`status ${scalar(trial.status)}`}>{scalar(trial.status)}</span></td><td>{scalar(object(trial.score ?? null)?.objective)}</td><td>{typeof trial.run_id === 'string' ? <button className="link-button" onClick={() => onOpenRun(trial.run_id as string)}>{trial.run_id}</button> : '—'}</td><td>{scalar(trial.error)}</td></tr>)}</tbody></table></div></>
}

function Optimization({ form, jobs, selectedJobId, submitOperation, onNotice, onCancel, onOpenRun }: CampaignProps) {
  const fields = useOptimizationFields(form)
  const run = () => {
    try { void submitOperation('optimize', optimizationRequest(backtestRequest(form), fields.dimension, fields.choices, fields.objective, fields.budget, fields.seed, fields.constraint, fields.threshold, fields.second, fields.secondChoices, fields.bound)) }
    catch (error) { onNotice(String(error)) }
  }
  return <div className="campaign-grid"><div className="panel"><div className="panel-head"><h2>Parameter optimisation</h2></div><p className="note">Uses the current Backtests setup as the pinned base request. Configure dataset, strategy and period there first.</p><OptimizationFields {...fields} /><button className="primary wide" onClick={run}>Run parameter search</button></div><div className="panel"><CampaignJob kind="optimize" jobs={jobs} selectedJobId={selectedJobId} onCancel={onCancel} onOpenRun={onOpenRun}>{job => job?.status === 'succeeded' ? <TrialResults result={job.result} onOpenRun={onOpenRun} /> : <div className="empty">The job journal retains individual trial progress. Results appear after completion.</div>}</CampaignJob></div></div>
}

function WalkForward({ form, jobs, selectedJobId, submitOperation, onNotice, onCancel, onOpenRun }: CampaignProps) {
  const fields = useOptimizationFields(form)
  const [training, setTraining] = useState('14')
  const [testing, setTesting] = useState('7')
  const [mode, setMode] = useState('rolling')
  const run = () => {
    try {
      const request = optimizationRequest(backtestRequest(form), fields.dimension, fields.choices, fields.objective, fields.budget, fields.seed, fields.constraint, fields.threshold, fields.second, fields.secondChoices, fields.bound)
      void submitOperation('walk-forward', { ...request, training_seconds: integer(training, 'Training days', 1) * 86400, test_seconds: integer(testing, 'Test days', 1) * 86400, mode })
    } catch (error) { onNotice(String(error)) }
  }
  return <div className="campaign-grid"><div className="panel"><div className="panel-head"><h2>Walk-forward setup</h2></div><p className="note">Frozen selections are evaluated on subsequent, separate out-of-sample windows. The same Backtests base configuration is used.</p><div className="form-grid"><Field label="Training days" value={training} onChange={setTraining} /><Field label="Test days" value={testing} onChange={setTesting} /><Select label="Window mode" value={mode} onChange={setMode} options={[{ value: 'rolling', label: 'Rolling' }, { value: 'expanding', label: 'Expanding' }]} /></div><OptimizationFields {...fields} /><button className="primary wide" onClick={run}>Run walk-forward analysis</button></div><div className="panel"><CampaignJob kind="walk-forward" jobs={jobs} selectedJobId={selectedJobId} onCancel={onCancel} onOpenRun={onOpenRun}>{job => job?.status === 'succeeded' ? <WalkForwardResults result={job.result} onOpenRun={onOpenRun} /> : <div className="empty">Window evidence appears after completion.</div>}</CampaignJob></div></div>
}

function WalkForwardResults({ result, onOpenRun }: { result: Json; onOpenRun: (id: string) => void }) {
  const windows = windowRows(result)
  const stitched = rows(object(result)?.stitched_oos_equity ?? null)
  return <>{stitched.length > 0 && <Plot title="Stitched out-of-sample equity" xTitle="UTC time" yTitle="Equity" traces={[{ type: 'scatter', mode: 'lines', x: stitched.map(row => scalar(row.timestamp)), y: stitched.map(row => Number(row.equity)), text: stitched.map(row => `Window ${scalar(row.window_index)}`) }]} />}{windows.length === 0 && <div className="empty">No completed windows.</div>}{windows.map(item => {
    const window = object(item.window ?? null)
    const selection = object(item.selection ?? null)
    const config = object(selection?.config ?? null)
    const oos = object(item.oos_run ?? null)
    const metricDeltas = rows(item.metric_deltas ?? null)
    return <div className="window-card" key={scalar(window?.index)}><h3>Window {scalar(window?.index)}</h3><div className="timeline"><div><strong>In sample</strong><span>{scalar(window?.train_start)} → {scalar(window?.train_end)}</span></div><div><strong>Out of sample</strong><span>{scalar(window?.oos_start)} → {scalar(window?.oos_end)}</span></div></div><p>Frozen parameters: <code>{scalar(config?.parameters)}</code></p><p>Selected IS run: <button className="link-button" onClick={() => typeof selection?.run_id === 'string' && onOpenRun(selection.run_id)}>{scalar(selection?.run_id)}</button> · OOS run: <button className="link-button" onClick={() => typeof oos?.run_id === 'string' && onOpenRun(oos.run_id)}>{scalar(oos?.run_id)}</button></p><div className="table-wrap"><table><thead><tr><th>Metric</th><th>IS</th><th>OOS</th><th>Delta</th><th>Reason</th></tr></thead><tbody>{metricDeltas.map((metric, index) => <tr key={index}><td>{scalar(metric.metric)}</td><td>{scalar(metric.in_sample)}</td><td>{scalar(metric.out_of_sample)}</td><td>{scalar(metric.delta)}</td><td>{scalar(metric.reason)}</td></tr>)}</tbody></table></div></div>
  })}</>
}

function MonteCarlo({ runs, jobs, selectedJobId, submitOperation, onNotice, onCancel, onOpenRun }: { runs: Run[]; jobs: Job[]; selectedJobId: string | null; submitOperation: SubmitOperation; onNotice: (value: string) => void; onCancel: (id: string) => void; onOpenRun: (id: string) => void }) {
  const [source, setSource] = useState('')
  const [method, setMethod] = useState('iid_return_bootstrap')
  const [count, setCount] = useState('100')
  const [length, setLength] = useState('40')
  const [seed, setSeed] = useState('17')
  const [equity, setEquity] = useState('10000')
  const [block, setBlock] = useState('5')
  const [percentile, setPercentile] = useState('p50')
  const run = () => {
    try {
      if (!source) throw new Error('Select a verified source run')
      const selected = runs.find(item => item.run_id === source)
      const config = object(selected?.config ?? null)
      const request: JsonObject = { source_run_ids: [source], method, sample_kind: 'periodic_return', path_count: integer(count, 'Path count', 1), path_length: integer(length, 'Path length', 1), seed: integer(seed, 'Seed'), periods_per_year: 252, currency: scalar(config?.currency ?? 'USD'), initial_equity: equity }
      if (method === 'moving_block_bootstrap') request.block_length = integer(block, 'Block length', 1)
      void submitOperation('monte-carlo', request)
    } catch (error) { onNotice(String(error)) }
  }
  return <div className="campaign-grid"><div className="panel"><div className="panel-head"><h2>Monte Carlo setup</h2></div><p className="note">Return samples come from the verified source run. Path results are simulated uncertainty, not a future forecast.</p><Select label="Source run" value={source} onChange={setSource} options={[{ value: '', label: 'Select a verified run' }, ...runs.map(item => ({ value: item.run_id, label: item.run_id }))]} /><Select label="Resampling method" value={method} onChange={setMethod} options={[{ value: 'iid_return_bootstrap', label: 'IID return bootstrap' }, { value: 'moving_block_bootstrap', label: 'Moving block bootstrap' }]} /><div className="form-grid"><Field label="Paths" value={count} onChange={setCount} /><Field label="Path length" value={length} onChange={setLength} /><Field label="Seed" value={seed} onChange={setSeed} /><Field label="Initial equity" value={equity} onChange={setEquity} />{method === 'moving_block_bootstrap' && <Field label="Block length" value={block} onChange={setBlock} />}</div><button className="primary wide" onClick={run}>Run Monte Carlo</button></div><div className="panel"><CampaignJob kind="monte-carlo" jobs={jobs} selectedJobId={selectedJobId} onCancel={onCancel} onOpenRun={onOpenRun}>{job => job?.status === 'succeeded' ? <MonteCarloResults result={job.result} percentile={percentile} setPercentile={setPercentile} /> : <div className="empty">Distribution evidence appears after completion.</div>}</CampaignJob></div></div>
}

function MonteCarloResults({ result, percentile, setPercentile }: { result: Json; percentile: string; setPercentile: (value: string) => void }) {
  const data = object(object(result)?.result ?? null)
  const paths = pathRows(result)
  const distributions = rows(data?.distributions ?? null)
  const terminal = distributions.find(item => item.metric === 'terminal_wealth')
  const drawdown = distributions.find(item => item.metric === 'maximum_drawdown')
  const traces = paths.slice(0, 100).map((path, index) => ({ type: 'scatter', mode: 'lines', name: `Path ${index + 1}`, x: Array.isArray(path.equity) ? path.equity.map((_, i) => i) : [], y: Array.isArray(path.equity) ? path.equity.map(value => Number(value)) : [], line: { color: 'rgba(57,214,197,0.12)', width: 1 }, showlegend: false }))
  const terminalValue = terminal?.[percentile]
  const drawdownValue = drawdown?.[percentile]
  const compact = (value: Json | undefined, unit: string) => displayMetric({ value: typeof value === 'string' ? value : null, unit, status: 'ok', reason: null, observations: 0 })
  return <><div className="kpis slim"><div><span>Paths</span><strong>{paths.length}</strong></div><div><span>Risk of ruin</span><strong title={scalar(data?.risk_of_ruin)}>{compact(data?.risk_of_ruin, 'fraction')}</strong></div><div><span>Metric basis</span><strong>{scalar(data?.metric_basis)}</strong></div></div><Select label="Distribution percentile" value={percentile} onChange={setPercentile} options={['p05', 'p25', 'p50', 'p75', 'p95'].map(value => ({ value, label: value.toUpperCase() }))} />{traces.length > 0 && <Plot title="Simulated equity fan · recorded paths" xTitle="Sample step" yTitle="Equity" traces={traces} height={380} />}<div className="two-columns distribution"><div className="metric"><span>Terminal wealth · {percentile.toUpperCase()}</span><strong title={scalar(terminalValue)}>{compact(terminalValue, 'currency')}</strong><small>{scalar(terminal?.observations)} observations</small></div><div className="metric"><span>Maximum drawdown · {percentile.toUpperCase()}</span><strong title={scalar(drawdownValue)}>{compact(drawdownValue, 'fraction')}</strong><small>{scalar(drawdown?.observations)} observations</small></div></div><div className="table-wrap"><table><thead><tr><th>Metric</th><th>P05</th><th>P25</th><th>P50</th><th>P75</th><th>P95</th><th>Unavailable</th></tr></thead><tbody>{distributions.map(item => <tr key={scalar(item.metric)}><td>{scalar(item.metric)}</td>{['p05', 'p25', 'p50', 'p75', 'p95', 'unavailable_observations'].map(key => <td key={key} title={scalar(item[key])}>{key.startsWith('p') ? compact(item[key], item.metric === 'maximum_drawdown' ? 'fraction' : 'currency') : scalar(item[key])}</td>)}</tr>)}</tbody></table></div></>
}

function Experiments({ runs, jobs, onOpenRun, onNotice }: { runs: Run[]; jobs: Job[]; onOpenRun: (id: string) => void; onNotice: (value: string) => void }) {
  const [selected, setSelected] = useState<string[]>([])
  const [comparison, setComparison] = useState<JsonObject | null>(null)
  const [busy, setBusy] = useState(false)
  const toggle = (id: string) => setSelected(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id])
  const compare = async () => {
    try { setBusy(true); setComparison(await api<JsonObject>('/compare', { method: 'POST', body: { run_ids: selected } })) }
    catch (error) { onNotice(String(error)) } finally { setBusy(false) }
  }
  const metrics = object(comparison?.metrics ?? null)
  const comparedIds = Array.isArray(comparison?.run_ids) ? comparison.run_ids.filter((value): value is string => typeof value === 'string') : []
  return <div className="two-columns"><div className="panel"><div className="panel-head"><h2>Verified experiments</h2><span className="tag">IMMUTABLE RUNS</span></div><p className="note">Choose two to twenty runs for a side-by-side comparison. Differing periods or currencies are flagged by the API.</p><div className="list">{runs.map(run => <div className="list-row" key={run.run_id}><label className="check"><input type="checkbox" checked={selected.includes(run.run_id)} onChange={() => toggle(run.run_id)} /><span><strong>{run.run_id}</strong><small className="mono">{run.result_fingerprint?.slice(0, 18)}…</small></span></label><button onClick={() => onOpenRun(run.run_id)}>Report</button></div>)}</div><button className="primary" disabled={selected.length < 2 || selected.length > 20 || busy} onClick={() => void compare()}>Compare selected ({selected.length})</button><h3>Job history</h3><JobList jobs={jobs.slice(0, 25)} onOpenRun={onOpenRun} /></div><div className="panel"><div className="panel-head"><h2>Comparison</h2></div>{!comparison ? <div className="empty">Select runs to compare their recorded metrics.</div> : <>{Array.isArray(comparison.warnings) && comparison.warnings.map((warning, index) => <div className="warning" key={index}>{scalar(warning)}</div>)}<p className="note">Values retain source units, status and undefined reasons. Hover over a cell for the exact API value. No strategy is ranked or promoted here.</p><div className="table-wrap"><table><thead><tr><th>Metric</th>{comparedIds.map(id => <th key={id} title={id}>{id.slice(0, 12)}</th>)}</tr></thead><tbody>{metrics && Object.entries(metrics).map(([name, value]) => { const byRun = object(value); return <tr key={name}><td>{name.replaceAll('_', ' ')}</td>{comparedIds.map(id => { const metric = object(byRun?.[id] ?? null); const raw = metric?.value; const shown = displayMetric({ value: typeof raw === 'string' ? raw : null, unit: typeof metric?.unit === 'string' ? metric.unit : '', status: typeof metric?.status === 'string' ? metric.status : 'unavailable', reason: typeof metric?.reason === 'string' ? metric.reason : null, observations: typeof metric?.observations === 'number' ? metric.observations : 0 }); return <td key={id} title={raw === null ? scalar(metric?.reason) : `${scalar(raw)} ${scalar(metric?.unit)}`}><span>{shown}</span>{metric?.status !== 'ok' && metric?.status !== 'available' && <small className="metric-state">{scalar(metric?.status)}{metric?.reason ? `: ${scalar(metric.reason)}` : ''}</small>}</td> })}</tr> })}</tbody></table></div></>}</div></div>
}
