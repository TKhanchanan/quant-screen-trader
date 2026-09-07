import { useEffect, useRef, useState, type JSX, type PointerEvent } from 'react'
import { adjustBounds, NormalizedBoundsSchema, type CalibrationDraft, type NormalizedBounds, type Platform } from '@quant-screen-trader/shared-types'

export function CalibrationOverlay({ platform }: { platform: Platform }): JSX.Element {
  const [draft, setDraft] = useState<CalibrationDraft | null>(null)
  const [error, setError] = useState('')
  const [opacity, setOpacity] = useState(0.15)
  const [activeSlot, setActiveSlot] = useState(1)
  const drag = useRef<{ id: number; x: number; y: number; bounds: NormalizedBounds; resize: boolean } | null>(null)
  useEffect(() => {
    document.documentElement.classList.add('overlay-document')
    void window.quantScreenTrader.platformCommand({ operation: 'state', platform })
      .then((s) => setDraft(s.draft)).catch(() => setError('Calibration unavailable'))
    return () => document.documentElement.classList.remove('overlay-document')
  }, [platform])
  const commit = (next: CalibrationDraft): void => {
    setDraft(next)
    void window.quantScreenTrader.platformCommand({ operation: 'draft', platform, draft: next })
      .catch(() => setError('Geometry could not be synchronized. Cancel and reopen before saving.'))
  }
  const move = (event: PointerEvent<HTMLDivElement>): void => {
    const current = drag.current
    if (!current || !draft) return
    const bounds = adjustBounds(current.bounds, (event.clientX - current.x) / window.innerWidth,
      (event.clientY - current.y) / window.innerHeight, current.resize)
    commit({ ...draft, slots: draft.slots.map((s) => s.id === current.id ? { ...s, bounds } : s) })
  }
  return <main className="calibration-canvas" aria-label="Calibration overlay">
    {draft?.slots.map((slot) => <div key={slot.id} className="calibration-box" data-slot-id={slot.id}
      style={{ zIndex: activeSlot === slot.id ? 10 : 1, left: `${slot.bounds.x * 100}%`, top: `${slot.bounds.y * 100}%`, width: `${slot.bounds.width * 100}%`, height: `${slot.bounds.height * 100}%`, background: `rgba(65,140,255,${opacity})` }}
      onPointerMove={move} onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
      <button className="drag-handle" aria-label={`Move slot ${slot.id}`} onPointerDown={(e) => {
        setActiveSlot(slot.id)
        e.currentTarget.parentElement?.setPointerCapture(e.pointerId)
        drag.current = { id: slot.id, x: e.clientX, y: e.clientY, bounds: slot.bounds, resize: false }
      }} onKeyDown={(e) => {
        if (!draft || !e.key.startsWith('Arrow')) return
        e.preventDefault()
        const bounds = adjustBounds(slot.bounds, e.key === 'ArrowLeft' ? -0.005 : e.key === 'ArrowRight' ? 0.005 : 0,
          e.key === 'ArrowUp' ? -0.005 : e.key === 'ArrowDown' ? 0.005 : 0, e.shiftKey)
        commit({ ...draft, slots: draft.slots.map((s) => s.id === slot.id ? { ...s, bounds } : s) })
      }}>SLOT {slot.id} · {NormalizedBoundsSchema.safeParse(slot.bounds).success ? 'VALID' : 'INVALID'}<br />
        {draft.assets.slots.find((s) => s.id === slot.id)?.assetName || 'Unassigned'}</button>
      <small>x {slot.bounds.x.toFixed(3)} · y {slot.bounds.y.toFixed(3)}<br />w {slot.bounds.width.toFixed(3)} · h {slot.bounds.height.toFixed(3)}</small>
      <button className="resize-handle" aria-label={`Resize slot ${slot.id}`} onPointerDown={(e) => {
        setActiveSlot(slot.id)
        e.currentTarget.parentElement?.setPointerCapture(e.pointerId)
        drag.current = { id: slot.id, x: e.clientX, y: e.clientY, bounds: slot.bounds, resize: true }
      }}>↘</button>
    </div>)}
    <div className="overlay-opacity"><label>Selected slot <select aria-label="Selected slot" value={activeSlot} onChange={(e) => setActiveSlot(Number(e.target.value))}>{draft?.slots.map((slot) => <option key={slot.id} value={slot.id}>{slot.id}</option>)}</select></label><label>Fill opacity <input aria-label="Overlay opacity" type="range" min="0" max="0.6" step="0.05" value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} /></label>{error}</div>
  </main>
}
