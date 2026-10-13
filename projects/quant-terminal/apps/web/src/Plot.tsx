import { useEffect, useRef, useState } from 'react'
import type { Chart } from './api'

type Trace = Record<string, unknown>

export function Plot({ traces, title, xTitle, yTitle, height = 330 }: {
  traces: Trace[]; title?: string; xTitle?: string; yTitle?: string; height?: number
}) {
  const node = useRef<HTMLDivElement>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    let dispose: (() => void) | undefined
    import('plotly.js-dist-min').then(({ default: Plotly }) => {
      if (!active || !node.current) return
      const target = node.current
      Plotly.react(target, traces, {
        title: title ? { text: title, font: { size: 14, color: '#d8e5f3' } } : undefined,
        height,
        margin: { l: 82, r: 22, t: title ? 48 : 20, b: 72 },
        paper_bgcolor: '#101c2d', plot_bgcolor: '#101c2d',
        font: { color: '#9eb3ca', family: 'Inter, Segoe UI, sans-serif', size: 11 },
        xaxis: { title: xTitle ? { text: xTitle } : undefined, gridcolor: '#21354c', zerolinecolor: '#33506e' },
        yaxis: { title: yTitle ? { text: yTitle } : undefined, gridcolor: '#21354c', zerolinecolor: '#33506e' },
        legend: { orientation: 'h', y: -0.3 },
        colorway: ['#39d6c5', '#7ca9ff', '#f3bd72', '#bd93f9', '#e67c98'],
        hovermode: 'closest',
      }, { responsive: true, displaylogo: false }).catch(err => setError(String(err)))
      dispose = () => Plotly.purge(target)
    }).catch(err => setError(String(err)))
    return () => { active = false; dispose?.() }
  }, [traces, title, xTitle, yTitle, height])
  return error ? <div className="error">Chart unavailable: {error}</div> : <div ref={node} className="plot" />
}

export function ReportChart({ chart, height }: { chart: Chart | null; height?: number }) {
  if (!chart || chart.series.length === 0) return <div className="empty">No chart observations available.</div>
  const traces = chart.series.map(series => ({
    name: series.name,
    type: chart.kind === 'bar' ? 'bar' : 'scatter',
    mode: chart.kind === 'scatter' ? 'markers' : chart.kind === 'line' ? 'lines' : undefined,
    x: series.points.map(point => point[0]),
    y: series.points.map(point => point[1] === null ? null : Number(point[1])),
    connectgaps: false,
    line: { width: 2 },
  }))
  return <><Plot traces={traces} title={chart.title} xTitle={chart.x_label} yTitle={chart.y_label} height={height} />{chart.note && <p className="note">{chart.note}</p>}</>
}
