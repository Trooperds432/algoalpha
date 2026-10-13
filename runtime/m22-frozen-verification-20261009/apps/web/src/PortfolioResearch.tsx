import { useState } from 'react'
import { api, type JsonObject } from './api'

const examples: Record<string, JsonObject> = {
  risk_parity: { covariance: [['0.04', '0'], ['0', '0.09']] },
  volatility_target: { weights: ['0.5', '0.5'], covariance: [['0.04', '0'], ['0', '0.09']], target: '0.10', max_leverage: '2' },
  mean_variance: { expected_returns: ['0.06', '0.08'], covariance: [['0.04', '0'], ['0', '0.09']], risk_aversion: '2.5' },
  black_litterman: { covariance: [['0.04', '0'], ['0', '0.09']], market_weights: ['0.5', '0.5'], views: [['1', '-1']], view_returns: ['0.01'], view_covariance: [['0.002']], tau: '0.05', risk_aversion: '2.5' },
  risk_contributions: { weights: ['0.6', '0.4'], covariance: [['0.04', '0'], ['0', '0.09']] },
  factor_exposures: { weights: ['0.6', '0.4'], loadings: [['1', '0.5'], ['0.8', '-0.2']] },
  factor_attribution: { weights: ['0.6', '0.4'], loadings: [['1', '0.5'], ['0.8', '-0.2']], factor_returns: ['0.01', '-0.02'], residual_returns: ['0.001', '0.002'] },
  factor_covariance: { loadings: [['1'], ['0.8']], factor_covariance_matrix: [['0.04']], idiosyncratic_variances: ['0.01', '0.02'] },
  stress_scenarios: { weights: ['0.6', '0.4'], asset_shocks: [['-0.2', '-0.1'], ['-0.4', '0.05']], nav: '100000' },
  correlation_regimes: { prior_returns: [['0.01', '0.02'], ['-0.01', '-0.02'], ['0.03', '0.06']], current_returns: [['0.01', '-0.02'], ['-0.01', '0.02'], ['0.03', '-0.06']], threshold: '0.2' },
  concentration: { weights: ['0.6', '0.4'] },
  liquidity_capacity: { position_values: ['60000', '40000'], adv_values: ['1000000', '500000'], weights: ['0.6', '0.4'], participation_limit: '0.1' },
}

export function PortfolioResearch() {
  const [operation, setOperation] = useState('risk_parity')
  const [input, setInput] = useState(JSON.stringify(examples.risk_parity, null, 2))
  const [result, setResult] = useState<JsonObject | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function run() {
    setError(''); setResult(null); setBusy(true)
    try {
      const inputs: unknown = JSON.parse(input)
      if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error('Enter a parameter object')
      setResult(await api<JsonObject>('/portfolio-research', { method: 'POST', body: { operation, inputs: inputs as JsonObject } }))
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  return <div className="two-columns">
    <section className="panel"><div className="panel-head"><h2>Portfolio & risk research</h2><span className="tag">LOCAL RESEARCH</span></div>
      <p className="note">Examples use synthetic aligned inputs. Annual covariance and expected excess returns apply to allocation models. Financial values are decimal strings and returns are fractions. Mean-variance permits shorts; stress shocks are supplied scenarios.</p>
      <label>Model<select value={operation} onChange={event => { const name = event.target.value; setOperation(name); setInput(JSON.stringify(examples[name], null, 2)); setResult(null); setError('') }}>{Object.keys(examples).map(name => <option key={name} value={name}>{name.replaceAll('_', ' ')}</option>)}</select></label>
      <label>Research parameters<textarea aria-label="Research parameters" rows={18} value={input} onChange={event => setInput(event.target.value)} /></label>
      <button disabled={busy} onClick={() => void run()}>{busy ? 'Calculating…' : 'Calculate research'}</button>
      {error && <p role="alert">{error}</p>}
    </section>
    <section className="panel"><div className="panel-head"><h2>Research result</h2></div><p className="note">Allocation outputs are research estimates. Liquidity capacity assumes constant dollar volume and excludes impact and slippage.</p>{result ? <pre aria-label="Research result">{JSON.stringify(result, null, 2)}</pre> : <p>Select a model and calculate to see its result and assumptions.</p>}</section>
  </div>
}
