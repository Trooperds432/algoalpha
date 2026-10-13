import { useState } from 'react'
import { api, type JsonObject } from './api'

import examplesData from './advancedAssetExamples.json'
const examples = examplesData as unknown as Record<string, JsonObject>

export function AdvancedAssets() {
  const [operation, setOperation] = useState('multi_currency_account')
  const [input, setInput] = useState(JSON.stringify(examples.multi_currency_account, null, 2))
  const [result, setResult] = useState<JsonObject | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function run() {
    setError(''); setResult(null); setBusy(true)
    try {
      const inputs: unknown = JSON.parse(input)
      if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error('Enter a parameter object')
      setResult(await api<JsonObject>('/advanced-assets', { method: 'POST', body: { operation, inputs: inputs as JsonObject } }))
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  return <div className="two-columns">
    <section className="panel"><div className="panel-head"><h2>Advanced asset models</h2><span className="tag">SYNTHETIC INPUTS</span></div>
      <p className="note">Native accounting and asset-specific research. These models are separate from the equity/ETF backtest and paper engine. Monetary inputs are decimal strings; contract counts and option multipliers are integers.</p>
      <p className="note">FX conversion rates are target/source; account valuation rates are base/native. Futures use supplied collateral. Option Greeks use European Black-Scholes with approximate normal probabilities; uncovered-equity margin is a research estimate.</p>
      <label>Asset model<select value={operation} onChange={event => { const name = event.target.value; setOperation(name); setInput(JSON.stringify(examples[name], null, 2)); setResult(null); setError('') }}>{Object.keys(examples).map(name => <option key={name} value={name}>{name.replaceAll('_', ' ')}</option>)}</select></label>
      <label>Asset parameters<textarea aria-label="Asset parameters" rows={20} value={input} onChange={event => setInput(event.target.value)} /></label>
      <button disabled={busy} onClick={() => void run()}>{busy ? 'Calculatingâ€¦' : 'Calculate asset model'}</button>
      {error && <p role="alert">{error}</p>}
    </section>
    <section className="panel"><div className="panel-head"><h2>Asset result</h2></div><p className="note">Continuous-contract series are non-tradable. Margin calls are reported and never funded automatically. Unsupported exercise and margin policies reject explicitly.</p>{result ? <pre aria-label="Asset result">{JSON.stringify(result, null, 2)}</pre> : <p>Select a model and calculate to inspect its result and conventions.</p>}</section>
  </div>
}

