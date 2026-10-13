import { useCallback, useEffect, useState } from 'react'
import { api, object, rows, scalar, type JsonObject } from './api'

export function Paper() {
  const [sessions, setSessions] = useState<JsonObject[]>([])
  const [notice, setNotice] = useState('')
  const [identity, setIdentity] = useState('')
  const [instrument, setInstrument] = useState('')
  const [capital, setCapital] = useState('10000')
  const [quantity, setQuantity] = useState('10')
  const [busy, setBusy] = useState(false)
  const refresh = useCallback(async () => {
    try { setSessions(await api<JsonObject[]>('/paper/sessions')) }
    catch (error) { setNotice(String(error)) }
  }, [])
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer) }, [refresh])
  const post = async (path: string, payload: JsonObject) => {
    setBusy(true); setNotice('')
    try {
      await api(path, { method: 'POST', body: payload })
      await refresh()
    } catch (error) { setNotice(String(error)); await refresh() }
    finally { setBusy(false) }
  }
  const create = () => void post('/paper/sessions', {
    session_id: identity,
    config: {
      instrument: { instrument_id: instrument, symbol: instrument, currency: 'USD', asset_class: 'etf', calendar: 'XNYS', timezone: 'America/New_York', tick_size: '0.01', quantity_step: '1' },
      strategy: 'buy_and_hold', parameters: { quantity }, initial_cash: capital, max_order_notional: capital,
      execution: { commission_per_unit: '0.1', slippage_kind: 'fixed', slippage_value: '0.01', maximum_participation: '0.01' }, stale_seconds: 120,
    },
  })
  return <>
    <div className="panel"><div className="panel-head"><h2>Paper trading</h2><span className="tag">VIRTUAL FUNDS · SIMULATED FILLS</span></div>
      <p className="note">Sessions use a local market-data feed and a simulated broker. No exchange or broker is connected. New and restarted sessions require reconciliation before processing observations.</p>
      {notice && <div className="error" role="alert">{notice}</div>}
      <div className="form-grid">
        <label className="field"><span className="field-label">Session name</span><input value={identity} onChange={event => setIdentity(event.target.value)} /></label>
        <label className="field"><span className="field-label">ETF instrument ID</span><input value={instrument} onChange={event => setInstrument(event.target.value)} /></label>
        <label className="field"><span className="field-label">Virtual capital (USD)</span><input value={capital} onChange={event => setCapital(event.target.value)} /></label>
        <label className="field"><span className="field-label">Buy and hold quantity</span><input value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
      </div>
      <p className="note">This form configures a USD ETF buy-and-hold session: commission $0.10 per unit, slippage $0.01, participation 1%, stale-data limit 120 seconds. The API supports all three built-in strategies and explicit execution settings.</p>
      <button disabled={busy || !identity || !instrument} onClick={create}>Create disconnected paper session</button>
    </div>
    {!sessions.length && <div className="empty">No paper sessions configured.</div>}
    {sessions.map(session => {
      const id = scalar(session.session_id), portfolio = object(session.portfolio) ?? {}, reconciliation = object(session.reconciliation) ?? {}
      return <div className="panel" key={id}><div className="panel-head"><h2>{id}</h2><span className="tag">{session.connected === true ? 'Connected to local feed' : 'Processing halted'}</span></div>
        <p className="note">{scalar(session.reason ?? 'Reconciled')} · Last accepted sequence {scalar(session.last_sequence)}</p>
        <div className="kpis"><div><span>Virtual cash</span><strong>{scalar(portfolio.cash)}</strong></div><div><span>Virtual NAV</span><strong>{scalar(portfolio.nav)}</strong></div><div><span>Reconciliation error</span><strong>{scalar(portfolio.reconciliation_error)}</strong></div><div><span>Orders</span><strong>{rows(session.orders).length}</strong></div></div>
        <div className="actions"><button disabled={busy} onClick={() => void post(`/paper/sessions/${encodeURIComponent(id)}/reconnect`, { event_id: crypto.randomUUID(), snapshot: reconciliation })}>Reconcile virtual broker and reconnect</button><button disabled={busy || session.connected !== true} onClick={() => void post(`/paper/sessions/${encodeURIComponent(id)}/disconnect`, { event_id: crypto.randomUUID(), reason: 'operator_disconnect' })}>Disconnect</button></div>
        <div className="table-wrap"><table><thead><tr><th>Order</th><th>Status</th><th>Filled</th><th>Remaining</th></tr></thead><tbody>{Object.entries(object(reconciliation.orders) ?? {}).map(([orderId, value]) => { const order = object(value) ?? {}; return <tr key={orderId}><td>{orderId}</td><td>{scalar(order.status)}</td><td>{scalar(order.filled_quantity)}</td><td>{scalar(order.remaining_quantity)}</td></tr> })}</tbody></table></div>
      </div>
    })}
  </>
}
