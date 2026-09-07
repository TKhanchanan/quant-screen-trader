import { useMemo, type JSX } from 'react'
import type { Platform, PlatformSlot } from '@quant-screen-trader/shared-types'
import { createPlaceholderSlots } from '../features/slots/createPlaceholderSlots'
import { PLATFORM_DETAILS } from '../platforms'
import { useAppStore } from '../state/appStore'
import { EngineStatus } from './EngineStatus'

interface WorkspaceProps {
  platform: Platform
}

function SlotCard({ slot }: { slot: PlatformSlot }): JSX.Element {
  return (
    <article className="slot-card" data-slot-id={slot.id}>
      <header className="slot-card__header">
        <span>SLOT {slot.id}</span>
        <span className="slot-card__disabled">DISABLED</span>
      </header>
      <div className="slot-card__asset">{slot.assetName}</div>
      <dl className="slot-data">
        <div><dt>Data</dt><dd>WAITING</dd></div>
        <div><dt>Price</dt><dd>—</dd></div>
        <div><dt>M1 regime</dt><dd>—</dd></div>
        <div><dt>M5 regime</dt><dd>—</dd></div>
        <div><dt>M10 context</dt><dd>—</dd></div>
        <div><dt>Signal</dt><dd>NO SIGNAL</dd></div>
        <div><dt>Confidence</dt><dd>—</dd></div>
        <div><dt>Rank</dt><dd>—</dd></div>
      </dl>
      <footer className="slot-card__footer">STATUS · DISABLED</footer>
    </article>
  )
}

export function Workspace({ platform }: WorkspaceProps): JSX.Element {
  const slots = useMemo(() => createPlaceholderSlots(platform), [platform])
  const details = PLATFORM_DETAILS[platform]
  const engineHealth = useAppStore((state) => state.engineHealth)

  return (
    <main className="app-shell workspace">
      <header className="workspace-header">
        <div>
          <p className="eyebrow">{details.name.toUpperCase()}</p>
          <h1>{details.name} Workspace</h1>
          <p className="page-subtitle">Nine placeholder slots · platform session arrives in Phase 2</p>
        </div>
        <EngineStatus health={engineHealth} />
      </header>

      <section className="slot-grid" aria-label={`${details.name} nine-slot workspace`}>
        {slots.map((slot) => <SlotCard key={slot.id} slot={slot} />)}
      </section>
    </main>
  )
}
