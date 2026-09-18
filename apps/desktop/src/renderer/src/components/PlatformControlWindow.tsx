import { useEffect, useRef, useState, type JSX } from 'react'
import { boardLabel, candidateLabel, leadLabel, defaultCalibration, percent, type AssetSyncState, type FeatureState, type MarketSnapshot, type OpportunityState, type Platform, type StrategyState } from '@quant-screen-trader/shared-types'
import { PLATFORM_DETAILS } from '../platforms'
import { useAppStore } from '../state/appStore'
import { EngineStatus } from './EngineStatus'
import { useWorkspaceStore } from '../state/workspaceStore'
import { AssetSetup } from './AssetSetup'
import { CalibrationControls } from './CalibrationControls'
import { AnalyticsPanel } from './AnalyticsPanel'
import { DailySessionPanel } from './DailySessionPanel'
import { PaperPanel } from './PaperPanel'
import { ReplayPanel } from './ReplayPanel'
import { PolicyPanel } from './PolicyPanel'
import { TradingControls } from './TradingControls'

interface WorkspaceProps {
  platform: Platform
}

export function PlatformControlWindow({ platform }: WorkspaceProps): JSX.Element {
  const details = PLATFORM_DETAILS[platform]
  const engineHealth = useAppStore((state) => state.engineHealth)
  const { data, session, busy, error, execute, setSession } = useWorkspaceStore()
  const [mode, setMode] = useState<'browser' | 'assets' | 'calibration'>('browser')
  const [market, setMarket] = useState<MarketSnapshot | null>(null)
  const [probing, setProbing] = useState(false)
  const [probe, setProbe] = useState<MarketSnapshot | null>(null)
  const [sync, setSync] = useState<AssetSyncState | null>(null)
  const syncRevision = useRef(-1)
  const [developer, setDeveloper] = useState(false)
  const [features, setFeatures] = useState<FeatureState | null>(null)
  const [strategy, setStrategy] = useState<StrategyState | null>(null)
  const [opportunity, setOpportunity] = useState<OpportunityState | null>(null)
  const [actionError, setActionError] = useState('')
  const [activeTab, setActiveTab] = useState('signal')
  const [showHelp, setShowHelp] = useState(false)
  const [tradingError, setTradingError] = useState('')

  useEffect(() => {
    void execute({ operation: 'get', platform })
    let disposed = false
    const poll = (): void => { void window.quantScreenTrader.platformCommand({ operation: 'state', platform })
      .then((s) => { if (!disposed) setSession(s.session) }).catch(() => { if (!disposed) setActionError('เชื่อมต่อหน้าต่างแพลตฟอร์มไม่ได้') }) }
    const dataPoll = (): void => { void window.quantScreenTrader.market({ operation: 'state', platform })
      .then(s => { if (!disposed) { setMarket(s) } }).catch(() => {}) }
    const syncPoll = (): void => { void window.quantScreenTrader.assetSync({ operation: 'state', platform }).then(s => {
      if (disposed) return
      setSync(s)
      if (syncRevision.current !== s.revision) void execute({ operation: 'get', platform }).then(result => {
        if (!disposed && result) syncRevision.current = s.revision
      })
    }).catch(() => {}) }
    const featurePoll = (): void => { void window.quantScreenTrader.features(platform)
      .then(s => { if (!disposed) setFeatures(s) }).catch(() => {}) }
    const strategyPoll = (): void => { void window.quantScreenTrader.strategy(platform)
      .then(s => { if (!disposed) setStrategy(s) }).catch(() => {}) }
    const opportunityPoll = (): void => { void window.quantScreenTrader.opportunities(platform)
      .then(s => { if (!disposed) setOpportunity(s) }).catch(() => {}) }
    featurePoll()
    strategyPoll()
    opportunityPoll()
    const featureTimer = window.setInterval(featurePoll, 2000)
    const strategyTimer = window.setInterval(strategyPoll, 2000)
    const opportunityTimer = window.setInterval(opportunityPoll, 2000)
    const syncTimer = window.setInterval(syncPoll, 1000)
    const dataTimer = window.setInterval(dataPoll, 500)
    poll()
    const timer = window.setInterval(poll, 1000)
    return () => { disposed = true; window.clearInterval(timer); window.clearInterval(dataTimer)
      window.clearInterval(syncTimer); window.clearInterval(featureTimer)
      window.clearInterval(strategyTimer); window.clearInterval(opportunityTimer) }
  }, [platform, execute, setSession])

  const close = (): void => {
    void window.quantScreenTrader.platformCommand({ operation: 'endCalibration', platform })
      .then(() => setMode('browser')).catch(() => setActionError('ปิดการปรับพื้นที่ไม่สำเร็จ'))
  }
  const calibrate = (): void => {
    if (!data) return
    const profile = data.calibrations.find((p) => p.id === data.activeCalibrationId)
    void window.quantScreenTrader.platformCommand({ operation: 'state', platform }).then(browser =>
      window.quantScreenTrader.platformCommand({ operation: 'beginCalibration', platform,
        draft: { assets: data.configuration, slots: profile?.slots ?? defaultCalibration(platform), zoomFactor: browser.zoomFactor } }))
      .then(() => setMode('calibration')).catch(() => setActionError('เปิดการปรับพื้นที่ไม่สำเร็จ'))
  }
  const syncOnce = async (): Promise<void> => {
    try {
      setActionError('')
      await execute({ operation: 'get', platform })
      const result = await window.quantScreenTrader.assetSync({ platform, operation: 'sync' })
      setSync(result); await execute({ operation: 'get', platform })
      if (result.error?.includes('CALIBRATION_ZOOM_MISMATCH')) calibrate()
    } catch { setActionError('ซิงก์สินทรัพย์ไม่สำเร็จ ข้อมูลเดิมยังอยู่') }
  }
  const probePrices = async (): Promise<void> => {
    if (!sync?.syncFresh) {
      setActionError('กรุณาซิงก์สินทรัพย์ให้เป็นปัจจุบันก่อนตรวจสอบราคา')
      return
    }
    setProbing(true); setActionError('')
    try {
      const result = await window.quantScreenTrader.market({ platform, operation: 'probe' })
      setProbe(result); setMarket(result)
    } catch (error) { setActionError(error instanceof Error ? error.message : 'ตรวจสอบราคาไม่ได้') }
    finally { setProbing(false) }
  }
  const observe = async (): Promise<void> => {
    try {
      if (!market?.running && !sync?.syncFresh) {
        setActionError('กรุณาซิงก์สินทรัพย์ให้เป็นปัจจุบันก่อนเริ่มสังเกตการณ์')
        return
      }
      if (!market?.running && !data?.configuration.slots.some(s => s.enabled && s.assetName)) await syncOnce()
      const current = await execute({ operation: 'get', platform })
      if (!market?.running && !current?.configuration.slots.some(s => s.enabled && s.assetName)) { setActionError('ยังไม่มีสินทรัพย์ กรุณาซิงก์หรือตั้งค่าสินทรัพย์'); return }
      if (!market?.running && !current?.activeCalibrationId) { setActionError('สินทรัพย์พร้อมแล้ว กรุณาปรับพื้นที่อ่านกราฟก่อนเริ่มสังเกตการณ์'); return }
      setActionError('')
      setMarket(await window.quantScreenTrader.market({ platform, operation: market?.running ? 'stop' : 'start' }))
    } catch (error) {
      const message = error instanceof Error ? error.message : 'เริ่มสังเกตการณ์ไม่สำเร็จ'
      setActionError(message)
      if (message.includes('CALIBRATION_ZOOM_MISMATCH')) calibrate()
    }
  }
  const board = opportunity?.board ?? null

  return <main className="combined-workspace-shell">
    <header className="combined-topbar">
      <div className="combined-topbar__brand">
        <div className={`platform-mark platform-mark--${platform}`} aria-hidden="true" style={{ width: 24, height: 24, fontSize: 10, borderRadius: 6 }}>
          {details.shortName}
        </div>
        <h1 style={{ fontSize: '1.2rem', margin: 0 }}>{details.name}</h1>
        <EngineStatus health={engineHealth} />
      </div>
      <div className="combined-topbar__info">
        <span>เซสชัน: {session?.state ?? 'UNKNOWN'}</span>
        <span>การโหลด: {session?.loadState ?? 'รอข้อมูล'}</span>
        <span>{new Date().toLocaleString('th-TH')}</span>
      </div>
      <div className="combined-topbar__actions">
        <button className="tab-button" onClick={() => void window.quantScreenTrader.platformCommand({ operation: 'reload', platform }).catch(() => setActionError('โหลดแพลตฟอร์มใหม่ไม่สำเร็จ'))}>
          <span className="nav-icon">🔄</span> โหลดแพลตฟอร์มใหม่
        </button>
        <button className="tab-button" onClick={() => void execute({ operation: 'get', platform })}>
          <span className="nav-icon">🔃</span> รีเฟรชข้อมูล
        </button>
        <button className="primary-button" style={{ width: 'auto', padding: '6px 12px' }} disabled={busy || sync?.busy || mode !== 'browser'} onClick={() => void syncOnce()}>
          ซิงก์สินทรัพย์
        </button>
        <button className="tab-button" disabled={!data || busy || mode !== 'browser'} onClick={() => setMode('assets')}>ตั้งค่าสินทรัพย์</button>
        <button className="tab-button" aria-expanded={showHelp} aria-controls="workspace-help" onClick={() => setShowHelp(!showHelp)}>วิธีใช้งาน</button>
      </div>
    </header>

    {showHelp && <div id="workspace-help" className="workspace-help">เข้าสู่ระบบโบรกเกอร์ → เลือกกราฟ → ซิงก์สินทรัพย์ → ปรับพื้นที่อ่านกราฟ → เริ่มสังเกตการณ์ · การส่งคำสั่งจริงต้องเปิดโหมด AUTO และยืนยันความพร้อมแยกต่างหาก</div>}
    <div className="combined-content">
      <div className="combined-main">
        {/* Slot Summary */}
        <div className="slot-summary-header">
          <h2 style={{ fontSize: '1rem', margin: '0 0 10px 0' }}>📊 ภาพรวมสินทรัพย์ · 9 ช่อง <span style={{ fontSize: '0.8rem', color: '#596579', fontWeight: 'normal' }}>สถานะล่าสุดของแต่ละสินทรัพย์ · ติดตามข้อมูลล่าสุด</span></h2>
        </div>
        <div className="slot-grid-modern">
          {Array.from({ length: 9 }, (_, i) => {
            const slot = data?.configuration.slots.find((s) => s.id === i + 1)
            const detected = sync?.detection?.slots.find(s => s.slotId === i + 1)
            const observed = market?.slots.find(s => s.slotId === i + 1)
            const isFresh = Boolean(sync?.syncFresh && detected)
            const configuredName = slot?.displayName || slot?.assetName || 'ยังไม่ตั้งค่า'

            return <div key={i} data-slot-id={i + 1} className="slot-card-modern">
              <div className="slot-card-modern__header">
                <span style={{ color: '#596579', fontWeight: 'bold' }}>{i + 1}. {configuredName}</span>
              </div>
              <div className="slot-card-modern__status" style={{ color: isFresh ? '#087b65' : '#92620b', fontSize: '0.75rem', fontWeight: 600 }}>
                {isFresh ? 'ยืนยันแล้ว' : 'รอยืนยัน'}
              </div>
              <div className="slot-price">{observed?.observation?.price ?? '—'}</div>
              <div className="slot-card-modern__body" style={{ fontSize: '0.75rem', marginTop: 8 }}>
                <div style={{ color: '#475569' }}>{observed?.state ?? 'PAUSED'}</div>
                <div style={{ color: '#596579' }}>คุณภาพราคา · {observed?.observation?.dataQuality.state ?? 'INVALID'}</div>
                <div style={{ color: '#596579', marginTop: 4 }}>1s {observed?.secondSamples ?? 0} · S5 {observed?.s5Samples ?? 0}</div>
                <div style={{ color: '#596579' }}>M1 {observed?.m1Samples ?? 0} {observed?.m1State ?? 'collecting'}</div>
              </div>
            </div>
          })}
        </div>

        {/* Errors & Alerts */}
        {(error || actionError || tradingError) && <p role="alert" className="error-banner" style={{ margin: '16px 0' }}>{error || actionError || tradingError}</p>}
        {sync?.syncStatus === 'SCANNING' && <p role="status" style={{ color: '#246bce', margin: '16px 0' }}>กำลังอ่านกราฟเพื่อซิงก์สินทรัพย์…</p>}

        {developer && <details open className="diagnostics"><summary>รายละเอียดระบบ</summary><p>ราคา: {probe?.slots.length ?? 0} ช่อง · ฟีเจอร์: {features ? 'ได้รับข้อมูลแล้ว' : 'รอข้อมูล'} · กลยุทธ์: {strategy ? 'ได้รับข้อมูลแล้ว' : 'รอข้อมูล'}</p><pre>{JSON.stringify({ probe, features, strategy }, null, 2)}</pre></details>}
        {/* Tab Navigation */}
        <div className="panel-tabs">
          {[
            { id: 'signal', icon: '📡', label: 'บอร์ดสัญญาณ' },
            { id: 'paper', icon: '📈', label: 'ผลจำลอง (Paper)' },
            { id: 'daily', icon: '📅', label: 'รอบวัน (Daily)' },
            { id: 'analytics', icon: '🔬', label: 'วิเคราะห์ผลย้อนหลัง' },
            { id: 'replay', icon: '⏪', label: 'จำลองย้อนหลัง' },
            { id: 'policy', icon: '⚙️', label: 'นโยบายปรับตัว' },
          ].map(t => (
            <button key={t.id} aria-pressed={activeTab === t.id} className={`panel-tab ${activeTab === t.id ? 'active' : ''}`} onClick={() => setActiveTab(t.id)}>
              <span className="nav-icon">{t.icon}</span> {t.label}
            </button>
          ))}
        </div>

        {/* Grid of Panels */}
        <div className="panels-grid">
          {(activeTab === 'signal' || activeTab === 'all') && (
            <div className="dashboard-panel">
              <div className="dashboard-panel__header">
                <h3>บอร์ดสัญญาณ (Signal Board)</h3>
                <span className="page-subtitle">{board ? 'อัปเดตจากระบบ' : 'รอข้อมูล'}</span>
              </div>
              <div className="dashboard-panel__body">
                <p style={{ fontSize: '0.8rem', margin: '0 0 10px 0' }}>
                  {boardLabel(board)} · {board ? `${board.receivedSlots}/${board.expectedSlots} ช่อง` : '—'}
                </p>
                {board?.watchlist.length
                  ? <ol style={{ margin: 0, paddingLeft: 20, fontSize: '0.8rem' }}>{board.watchlist.map(entry => <li key={entry.slotId}>{candidateLabel(entry)}</li>)}</ol>
                  : <p style={{ fontSize: '0.8rem', color: '#596579' }}>รอบนี้ไม่มีตัวเลือกที่มีทิศทาง</p>}
                <p style={{ fontSize: '0.8rem', marginTop: 10 }}>{leadLabel(board)}</p>
              </div>
            </div>
          )}

          {(activeTab === 'paper' || activeTab === 'all') && (
             <div className="dashboard-panel-wrapper"><PaperPanel platform={platform} /></div>
          )}

          {(activeTab === 'daily' || activeTab === 'all') && (
            <div className="dashboard-panel-wrapper"><DailySessionPanel onError={setTradingError} /></div>
          )}

          {(activeTab === 'analytics' || activeTab === 'all') && (
            <div className="dashboard-panel-wrapper"><AnalyticsPanel platform={platform} /></div>
          )}

          {(activeTab === 'replay' || activeTab === 'all') && (
             <div className="dashboard-panel-wrapper"><ReplayPanel /></div>
          )}

          {(activeTab === 'policy' || activeTab === 'all') && (
             <div className="dashboard-panel-wrapper"><PolicyPanel /></div>
          )}
        </div>
      </div>

      <aside className="combined-sidebar">
        <h2 style={{ fontSize: '1rem', display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 16px 0' }}>
          <span className="nav-icon">⚙️</span> แผงควบคุม
        </h2>

        <div className="sidebar-section">
          <div className="sidebar-section__header">01 / ติดตามตลาด</div>
          <button className="primary-button" style={{ marginBottom: 10 }} disabled={busy || sync?.busy || mode !== 'browser'} onClick={() => void observe()}>
            ▶ {market?.running ? 'หยุดสังเกตการณ์' : 'เริ่มสังเกตการณ์'}
          </button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.75rem', marginBottom: 10 }}>
            <span style={{ color: '#596579' }}>อ่านข้อมูลทุก</span>
            <select style={{ flex: 1 }} value={market?.intervalMs ?? (platform === 'capitalbear' ? 500 : 1000)} onChange={e => void window.quantScreenTrader.market({ platform, operation: 'state', intervalMs: Number(e.target.value) }).then(setMarket).catch(() => setActionError('ปรับความถี่ไม่สำเร็จ กรุณาลองอีกครั้ง'))} aria-label="ความถี่ในการอ่านข้อมูล">
              {[250, 500, 1000, 2000].map(ms => <option key={ms} value={ms}>{ms} มิลลิวินาที</option>)}
            </select>
          </div>
          <label style={{ fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
            <input type="checkbox" checked={developer} onChange={e => setDeveloper(e.target.checked)} /> แสดงรายละเอียดระบบ
          </label>
          <div style={{ display: 'flex', gap: 8, fontSize: '0.7rem', flexWrap: 'wrap' }}>
            <span style={{ color: '#087b65' }}><span className="status__dot" style={{background: '#087b65'}}/> พร้อม {market?.slots.filter(s => s.state === 'READY').length ?? 0}</span>
            <span style={{ color: '#92620b' }}><span className="status__dot" style={{background: '#92620b'}}/> ไม่แน่นอน {market?.slots.filter(s => s.state === 'DATA_UNCERTAIN').length ?? 0}</span>
            <span style={{ color: '#b42332' }}><span className="status__dot" style={{background: '#b42332'}}/> ข้อมูลเก่า {market?.slots.filter(s => s.state === 'STALE').length ?? 0}</span>
          </div>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-section__header">02 / เตรียมสินทรัพย์และกราฟ</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
            <button className="sidebar-button" disabled={busy || mode !== 'browser'} onClick={() => void window.quantScreenTrader.platformCommand({ operation: 'closePortfolio', platform }).then(() => setActionError('')).catch(() => setActionError('ปิดแผงพอร์ตไม่สำเร็จ'))}>ปิดแผงพอร์ต</button>
            <button className="sidebar-button" disabled={!data || busy || mode !== 'browser'} onClick={calibrate}>ปรับพื้นที่อ่านกราฟ</button>
            <button className="sidebar-button" disabled={!data || busy || mode !== 'browser'} onClick={() => setMode('assets')}>ตั้งค่าสินทรัพย์</button>
            <button className="sidebar-button" disabled={busy || sync?.busy || mode !== 'browser'} onClick={() => void syncOnce()}>ซิงก์สินทรัพย์</button>
          </div>
          <label style={{ fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <input type="checkbox" checked={sync?.auto ?? false} onChange={e => void window.quantScreenTrader.assetSync({ platform, operation: 'auto', enabled: e.target.checked }).then(setSync).catch(() => setActionError('เปิดซิงก์อัตโนมัติไม่สำเร็จ'))} /> ซิงก์สินทรัพย์อัตโนมัติ
          </label>
          <button className="sidebar-button" style={{ width: '100%' }} disabled={busy || probing || sync?.busy || mode !== 'browser'} onClick={() => void probePrices()}>{probing ? 'กำลังตรวจสอบราคา…' : 'ตรวจสอบราคา'}</button>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-section__header">03 / คำสั่งซื้อขาย</div>
          <div className="trading-controls-wrapper">
             <TradingControls platform={platform} onError={setTradingError} />
          </div>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-section__header">สถานะปัจจุบัน</div>
          <EngineStatus health={engineHealth} />
          <p className="card-hint">{market?.running ? 'กำลังสังเกตการณ์' : 'หยุดสังเกตการณ์'} · {sync?.syncFresh ? 'สินทรัพย์ยืนยันแล้ว' : 'รอซิงก์สินทรัพย์'}</p>
        </div>
      </aside>
    </div>

    <div className="control-region">
      {mode === 'assets' && data && <AssetSetup platform={platform} initialSlots={data.configuration.slots} onClose={() => setMode('browser')} />}
    </div>
    {mode === 'calibration' && <CalibrationControls platform={platform} onClose={close} onError={setActionError} />}
  </main>
}

export function PriceReading({ observation }: { observation: MarketSnapshot['slots'][number]['observation'] }): JSX.Element {
  return <>Price {observation?.price ?? '—'} · {observation?.dataQuality.state ?? 'INVALID'} {observation?.dataQuality.state === 'UNCERTAIN' ? percent(observation.dataQuality.confidence) : ''}</>
}
