import { useEffect, useRef, useState, type JSX } from 'react'
import { defaultCalibration, type AssetSyncState, type MarketSnapshot, type Platform } from '@quant-screen-trader/shared-types'
import { PLATFORM_DETAILS } from '../platforms'
import { useAppStore } from '../state/appStore'
import { EngineStatus } from './EngineStatus'
import { useWorkspaceStore } from '../state/workspaceStore'
import { AssetSetup } from './AssetSetup'
import { CalibrationControls } from './CalibrationControls'

interface WorkspaceProps {
  platform: Platform
}

export function Workspace({ platform }: WorkspaceProps): JSX.Element {
  const details = PLATFORM_DETAILS[platform]
  const engineHealth = useAppStore((state) => state.engineHealth)
  const { data, session, busy, error, execute, setSession } = useWorkspaceStore()
  const [mode, setMode] = useState<'browser' | 'assets' | 'calibration'>('browser')
  const [now, setNow] = useState(0)
  const [market, setMarket] = useState<MarketSnapshot | null>(null)
  const [sync, setSync] = useState<AssetSyncState | null>(null)
  const syncRevision = useRef(-1)
  const [developer, setDeveloper] = useState(false)
  const [actionError, setActionError] = useState('')
  const region = useRef<HTMLDivElement>(null)
  useEffect(() => {
    void execute({ operation: 'get', platform })
    let disposed = false
    const poll = (): void => { void window.quantScreenTrader.platformCommand({ operation: 'state', platform })
      .then((s) => { if (!disposed) setSession(s.session) }).catch(() => { if (!disposed) setActionError('Platform browser unavailable') }) }
    const dataPoll = (): void => { void window.quantScreenTrader.market({ operation: 'state', platform })
      .then(s => { if (!disposed) { setMarket(s); setNow(Date.now()) } }).catch(() => {}) }
    const syncPoll = (): void => { void window.quantScreenTrader.assetSync({ operation: 'state', platform }).then(s => {
      if (disposed) return
      setSync(s)
      if (syncRevision.current !== s.revision) { syncRevision.current = s.revision; void execute({ operation: 'get', platform }) }
    }).catch(() => {}) }
    const syncTimer = window.setInterval(syncPoll, 1000)
    const dataTimer = window.setInterval(dataPoll, 500)
    poll()
    const timer = window.setInterval(poll, 1000)
    return () => { disposed = true; window.clearInterval(timer); window.clearInterval(dataTimer); window.clearInterval(syncTimer) }
  }, [platform, execute, setSession])
  useEffect(() => {
    const element = region.current
    if (!element) return
    const layout = (): void => {
      const rect = element.getBoundingClientRect()
      const x = Math.ceil(rect.x), y = Math.ceil(rect.y)
      const width = Math.floor(rect.right) - x, height = Math.floor(rect.bottom) - y
      if (width < 1 || height < 1) return
      void window.quantScreenTrader.platformCommand({ operation: 'layout', platform,
        bounds: { x, y, width, height }, visible: mode !== 'assets' }).catch(() => setActionError('Browser layout unavailable; resize the window to retry.'))
    }
    const observer = new ResizeObserver(layout)
    observer.observe(element); layout()
    window.addEventListener('resize', layout)
    return () => { observer.disconnect(); window.removeEventListener('resize', layout) }
  }, [platform, mode])
  const close = (): void => {
    void window.quantScreenTrader.platformCommand({ operation: 'endCalibration', platform })
      .then(() => setMode('browser')).catch(() => setActionError('Could not close calibration'))
  }
  const calibrate = (): void => {
    if (!data) return
    const profile = data.calibrations.find((p) => p.id === data.activeCalibrationId)
    void window.quantScreenTrader.platformCommand({ operation: 'beginCalibration', platform,
      draft: { assets: data.configuration, slots: profile?.slots ?? defaultCalibration(), zoomFactor: profile?.zoomFactor ?? 1 } })
      .then(() => setMode('calibration')).catch(() => setActionError('Could not open calibration'))
  }
  const syncOnce = async (): Promise<void> => {
    try {
      const result = await window.quantScreenTrader.assetSync({ platform, operation: 'sync' })
      setSync(result); await execute({ operation: 'get', platform })
    } catch { setActionError('Asset sync unavailable. Existing assets were preserved.') }
  }
  const observe = async (): Promise<void> => {
    try {
      if (!market?.running && !data?.configuration.slots.some(s => s.enabled && s.assetName)) await syncOnce()
      const current = await execute({ operation: 'get', platform })
      if (!market?.running && !current?.configuration.slots.some(s => s.enabled && s.assetName)) { setActionError('No identified assets. Sync Assets or use Asset Setup.'); return }
      if (!market?.running && !current?.activeCalibrationId) { setActionError('Assets are ready. Calibrate Slots before starting observation.'); return }
      setMarket(await window.quantScreenTrader.market({ platform, operation: market?.running ? 'stop' : 'start' }))
    } catch { setActionError('Observation unavailable') }
  }
  return <main className="workspace-shell">
    <header className="workspace-toolbar">
      <div className="toolbar"><h1>{details.name}</h1><EngineStatus health={engineHealth} />
        <span>Session: {session?.state ?? 'STARTING'} · Load: {session?.loadState ?? 'idle'}</span>
        <button onClick={() => void window.quantScreenTrader.platformCommand({ operation: 'reload', platform }).catch(() => setActionError('Reload failed'))}>Reload Platform</button>
        <button disabled={!data || busy || mode !== 'browser'} onClick={calibrate}>Calibrate Slots</button>
        <button disabled={!data || busy || mode !== 'browser'} onClick={() => setMode('assets')}>Asset Setup</button>
        <button disabled={busy || sync?.busy || mode !== 'browser'} onClick={() => void syncOnce()}>Sync Assets</button>
        <label><input type="checkbox" checked={sync?.auto ?? false} onChange={e => void window.quantScreenTrader.assetSync({ platform, operation: 'auto', enabled: e.target.checked }).then(setSync).catch(() => setActionError('Auto Sync unavailable'))} /> Auto Sync Assets</label>
        <button disabled={busy || mode !== 'browser'} onClick={() => void execute({ operation: 'get', platform })}>Refresh configuration</button>
      </div>
      <div className="toolbar">
        <button disabled={busy || sync?.busy || mode !== 'browser'} onClick={() => void observe()}>{market?.running ? 'Stop observation' : 'Start observation'}</button>
        <label>Sampling <select value={market?.intervalMs ?? (platform === 'capitalbear' ? 500 : 1000)} onChange={e => void window.quantScreenTrader.market({ platform, operation: 'state', intervalMs: Number(e.target.value) }).then(setMarket)}>
          {[250, 500, 1000, 2000].map(ms => <option key={ms} value={ms}>{ms} ms target</option>)}</select></label>
        <label><input type="checkbox" checked={developer} onChange={e => setDeveloper(e.target.checked)} /> Developer diagnostics</label>
        <span>Enabled {data?.configuration.slots.filter(s => s.enabled).length ?? 0} · Healthy {market?.slots.filter(s => s.state === 'READY').length ?? 0} · Uncertain {market?.slots.filter(s => s.state === 'DATA_UNCERTAIN').length ?? 0} · Stale {market?.slots.filter(s => s.state === 'STALE').length ?? 0} · {market?.captureRate.toFixed(1) ?? 0} obs/s · Queue {market?.queueDepth ?? 0} · Engine {market?.engineAvailable ? 'receiving' : 'waiting'}</span>
      </div>
      <p>Login manually in the platform. Login status is unverified; READY is never inferred from page load.</p>
      {sync?.detection && <p role="status">Asset sync: Detected {sync.detection.slots.filter(s => s.state === 'DETECTED').length} · Uncertain {sync.detection.slots.filter(s => s.state === 'UNCERTAIN').length} · Not found {sync.detection.slots.filter(s => s.state === 'NOT_FOUND').length} · Applied {sync.applied} · Manual slots preserved {sync.manualPreserved}</p>}
      {sync?.error && <p role="alert">{sync.error}</p>}
      {session?.errorMessage && <p role="alert" className="error-banner">{session.errorMessage}</p>}
      {(error || actionError) && <p role="alert" className="error-banner">{error || actionError}</p>}
      <div className="slot-summary" aria-label="Nine configured slots">{Array.from({ length: 9 }, (_, i) => {
        const slot = data?.configuration.slots.find((s) => s.id === i + 1)
        const detected = sync?.detection?.slots.find(s => s.slotId === i + 1)
        const observed = market?.slots.find(s => s.slotId === i + 1)
        return <span key={i} data-slot-id={i + 1}>{i + 1} · {slot?.displayName || slot?.assetName || 'Unassigned'}{slot?.enabled ? '' : ' (off)'} · {slot?.assetMode ?? 'AUTO'}<br />{detected?.state === 'UNCERTAIN' ? 'ASSET UNCERTAIN' : ''} {detected ? `${detected.source} ${Math.round(detected.confidence * 100)}%` : ''}<br />{observed?.state ?? 'WAITING'} {observed?.observation?.sourceType ?? ''}<br />Price {observed?.state === 'READY' ? observed.observation?.price : '—'} · {observed?.observation?.dataQuality.state ?? '—'}
          {observed?.observation && <small> · Age {Math.max(0, now - Date.parse(observed.observation.observedAt))} ms</small>}
          <small><br />1s {observed?.secondSamples ?? 0} · M1 {observed?.m1Samples ?? 0} {observed?.m1State ?? 'collecting'}</small>
        </span>
      })}</div>
      {developer && <details><summary>Slot diagnostics (images are not stored)</summary><pre style={{ maxHeight: 200, overflow: 'auto' }}>{JSON.stringify({ market, sync, calibration: data?.calibrations.find(p => p.id === data.activeCalibrationId) }, null, 2)}</pre></details>}
      {mode === 'calibration' && <CalibrationControls platform={platform} onClose={close} onError={setActionError} />}
    </header>
    <div ref={region} className="browser-region" aria-label={`${details.name} platform browser`}>
      {mode === 'assets' && data && <AssetSetup platform={platform} initialSlots={data.configuration.slots} onClose={() => setMode('browser')} />}
    </div>
  </main>
}
