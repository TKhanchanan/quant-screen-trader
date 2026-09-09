import { useEffect, useRef, useState, type JSX, type PointerEvent } from 'react'
import { adjustBounds, chartGridBounds, deriveChartGrid, NormalizedBoundsSchema,
  type CalibrationDraft, type NormalizedBounds, type Platform } from '@quant-screen-trader/shared-types'

export function CalibrationOverlay({ platform }: { platform: Platform }): JSX.Element {
  const [draft, setDraft] = useState<CalibrationDraft | null>(null)
  const [error, setError] = useState('')
  const [opacity, setOpacity] = useState(0.15)
  const drag = useRef<{ x: number; y: number; bounds: NormalizedBounds; resize: boolean } | null>(null)
  useEffect(() => {
    document.documentElement.classList.add('overlay-document')
    void window.quantScreenTrader.platformCommand({ operation: 'state', platform })
      .then(snapshot => setDraft(snapshot.draft)).catch(() => setError('Calibration unavailable'))
    return () => document.documentElement.classList.remove('overlay-document')
  }, [platform])
  const commitBounds = (bounds: NormalizedBounds): void => {
    if (!draft) return
    const slots = deriveChartGrid(platform, bounds, 'MANUAL').slots.map(slot => ({ id: slot.slotId, bounds: slot.chartBounds }))
    const next = { ...draft, slots }
    setDraft(next)
    void window.quantScreenTrader.platformCommand({ operation: 'draft', platform, draft: next })
      .catch(() => setError('Geometry could not be synchronized. Cancel and reopen before saving.'))
  }
  const move = (event: PointerEvent<HTMLDivElement>): void => {
    const current = drag.current
    if (!current) return
    commitBounds(adjustBounds(current.bounds, (event.clientX - current.x) / window.innerWidth,
      (event.clientY - current.y) / window.innerHeight, current.resize))
  }
  const bounds = draft ? chartGridBounds(draft.slots) : null
  return <main className="calibration-canvas" aria-label="Chart-area calibration overlay">
    {draft && bounds && <div className="calibration-box chart-grid-box"
      style={{ left: `${bounds.x * 100}%`, top: `${bounds.y * 100}%`, width: `${bounds.width * 100}%`,
        height: `${bounds.height * 100}%`, background: `rgba(65,140,255,${opacity})` }}
      onPointerMove={move} onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
      <button className="drag-handle" aria-label="Move chart area" onPointerDown={event => {
        event.currentTarget.parentElement?.setPointerCapture(event.pointerId)
        drag.current = { x: event.clientX, y: event.clientY, bounds, resize: false }
      }} onKeyDown={event => {
        if (!event.key.startsWith('Arrow')) return
        event.preventDefault()
        commitBounds(adjustBounds(bounds, event.key === 'ArrowLeft' ? -.005 : event.key === 'ArrowRight' ? .005 : 0,
          event.key === 'ArrowUp' ? -.005 : event.key === 'ArrowDown' ? .005 : 0, event.shiftKey))
      }}>CHART AREA · {NormalizedBoundsSchema.safeParse(bounds).success ? 'VALID' : 'INVALID'}<br />
        x {bounds.x.toFixed(3)} · y {bounds.y.toFixed(3)} · w {bounds.width.toFixed(3)} · h {bounds.height.toFixed(3)}</button>
      <div className="chart-grid-preview" aria-label="Derived Slot 1 through Slot 9">
        {draft.slots.map(slot => <span key={slot.id}>SLOT {slot.id}<small>{draft.assets.slots.find(asset => asset.id === slot.id)?.assetName || 'Unassigned'}</small></span>)}
      </div>
      <button className="resize-handle" aria-label="Resize chart area" onPointerDown={event => {
        event.currentTarget.parentElement?.setPointerCapture(event.pointerId)
        drag.current = { x: event.clientX, y: event.clientY, bounds, resize: true }
      }}>↘</button>
    </div>}
    <div className="overlay-opacity"><label>Fill opacity <input aria-label="Overlay opacity" type="range" min="0" max="0.6" step="0.05" value={opacity} onChange={event => setOpacity(Number(event.target.value))} /></label>{error}</div>
  </main>
}
