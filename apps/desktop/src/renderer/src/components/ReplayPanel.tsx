import { useEffect, useRef, useState, type JSX } from 'react'
import { REPLAY_NOT_APPLIED_NOTICE, emptyReplayState, replayFoldLine, replayLatencyLine,
  replayMoneyLine, replayPercentLabel, replayTallyLine, replayWarningLabel,
  type Platform, type ReplayState } from '@quant-screen-trader/shared-types'

const POLL_MS = 2_000
const DAY_MS = 86_400_000

const WINDOWS: { label: string; value: number | null }[] = [
  { label: 'ทั้งหมดที่มี', value: null },
  { label: '1 วันล่าสุด', value: DAY_MS },
  { label: '7 วันล่าสุด', value: 7 * DAY_MS },
  { label: '30 วันล่าสุด', value: 30 * DAY_MS }
]

const PLATFORMS: { label: string; value: Platform | null }[] = [
  { label: 'ทั้งสองโบรก', value: null },
  { label: 'CapitalBear', value: 'capitalbear' },
  { label: 'IQ Option', value: 'iqoption' }
]

const LATENCY = [100, 250, 500, 1000]

function stamp(value: number | null): string {
  return value === null ? '—' : new Date(value).toISOString().replace('T', ' ').slice(0, 19)
}

/**
 * Everything one finished replay says, as a pure function of the summary.
 *
 * Split from the polling shell so it can be rendered against a known result in a test. A panel
 * whose numbers are only ever exercised against an empty state is a panel whose formatting
 * nobody has checked.
 */
export function ReplayResult({ state }: { state: ReplayState }): JSX.Element {
  const summary = state.summary
  if (!summary) return <p>ยังไม่มีผลการจำลอง</p>
  const dataset = summary.dataset
  const walk = summary.walkForward
  return <>
    {summary.warnings.map(code => <p key={code} role="status" className="analytics-warning">
      {replayWarningLabel(code)}</p>)}

    <div className="analytics-card">
      <h4>ชุดข้อมูลที่ใช้</h4>
      <p>เริ่มจากชั้น {dataset.entryLayer} · โหมด {dataset.sourceMode}
        {' · '}เหตุการณ์ {dataset.events.toLocaleString()}</p>
      <p>{stamp(dataset.startTime)} → {stamp(dataset.endTime)}
        {' · '}สินทรัพย์ {dataset.assetsSeen} · บริบท {dataset.contextsSeen}</p>
      <p>ช่องว่างข้อมูล {dataset.gapSeconds.toLocaleString()} วินาที
        {' '}({replayPercentLabel(dataset.gapShare)}) — เก็บไว้ตามจริง ไม่ได้เติมค่าให้</p>
      <small>ลายนิ้วมือข้อมูล {dataset.inputFingerprint.slice(0, 16)}…
        {' · '}ตรวจสอบเหตุ-ผล {summary.causality.checks.toLocaleString()} ครั้ง
        {' · '}ผิดกติกา {summary.causality.violations}</small>
    </div>

    <div className="analytics-card">
      <h4>ผลฐาน (ไปป์ไลน์ปัจจุบัน ไม่ปรับอะไรเลย)</h4>
      {summary.platforms.map(tally => <p key={tally.platform ?? 'all'}>
        <strong>{tally.platform === 'capitalbear' ? 'CapitalBear' : 'IQ Option'}</strong>
        {' · '}{replayTallyLine(tally)}<br />
        <small>{replayMoneyLine(tally)} · ชนะติดกันสูงสุด {tally.maxWinStreak}
          {' · '}แพ้ติดกันสูงสุด {tally.maxLossStreak}</small></p>)}
      <p>รวมทั้งสองโบรก: {replayTallyLine(summary.overall)}</p>
      <small>CapitalBear วัดคำถาม 5 วินาที และ IQ Option วัดคำถาม 60 วินาที
        {' '}— ตัวเลขรวมจึงอยู่ใต้ตัวเลขแยกเสมอ ไม่ใช่แทนกัน</small>
    </div>

    <div className="analytics-card">
      <h4>ความครอบคลุมของประวัติ</h4>
      <p>สภาพตลาดหลัก {summary.coverage.dominantRegime ?? '—'}
        {' '}({replayPercentLabel(summary.coverage.dominantRegimeShare)})
        {' · '}สินทรัพย์ที่กินสัดส่วนมากสุด {summary.coverage.topAsset ?? '—'}
        {' '}({replayPercentLabel(summary.coverage.topAssetShare)})</p>
      <p>ครอบคลุม {summary.coverage.hoursCovered} ชั่วโมงของวัน
        {' · '}{summary.coverage.weekdaysCovered} วันในสัปดาห์
        {' · '}{summary.coverage.tradingDates} วันที่ต่างกัน
        {' · '}เขตเวลา {summary.coverage.timezone}</p>
    </div>

    <div className="analytics-card">
      <h4>Walk-forward (แบ่งตามเวลา ไม่สุ่ม)</h4>
      {walk && walk.rows.length
        ? <ol className="analytics-bins">{walk.rows.map(fold =>
            <li key={fold.foldId} className={fold.directionalStable ? 'threshold-stable' : 'threshold-unstable'}>
              {replayFoldLine(fold)}
              {fold.warnings.length ? <small> · {fold.warnings.join(', ')}</small> : null}</li>)}</ol>
        : <p>ยังแบ่งช่วงไม่พอสำหรับ walk-forward</p>}
      {walk && <p>ช่วงทั้งหมด {walk.folds} · พบเกณฑ์ {walk.foldsWithCandidate}
        {' · '}ทิศทางบวก {walk.foldsDirectionalPositive} · เงินบวก {walk.foldsMonetaryPositive}
        {' · '}ความนิ่ง {walk.candidateStability}</p>}
      <small>ค้นเกณฑ์จากช่วง “ฝึก” เท่านั้น แล้ววัดกับ “ตรวจ” และ “ทดสอบ” ที่ยังไม่เคยเห็น
        {' '}· มีช่วงกันชน (purge/embargo) ที่ขอบทุกช่วง เพื่อไม่ให้ไม้ที่ยังไม่จบคาบเกี่ยวสองฝั่ง</small>
    </div>

    <div className="analytics-card">
      <h4>ความไวต่อความหน่วง (SIMULATION ONLY)</h4>
      {summary.latency.length
        ? <ol className="analytics-bins">{summary.latency.map(row =>
            <li key={row.delayMs}>{replayLatencyLine(row)}</li>)}</ol>
        : <p>ไม่ได้ตั้งสถานการณ์ความหน่วงไว้</p>}
      <small>เป็นการทดสอบความทนทาน ไม่ใช่การเลือกค่าหน่วงที่ “ดีที่สุด”
        {' '}— ค่าที่บังเอิญดูดีในอดีตไม่ใช่ค่าที่ควรตั้งใช้จริง</small>
    </div>

    <p role="alert" className="analytics-notice">{REPLAY_NOT_APPLIED_NOTICE}</p>
  </>
}

/**
 * Phase 11 research: what the frozen pipeline would have decided over recorded history.
 *
 * Deliberately collapsed and deliberately without an Apply button. Everything in it describes a
 * market that has already happened; there is no bridge method that would accept a threshold from
 * here, and the engine ships no endpoint that would take one.
 */
export function ReplayPanel(): JSX.Element {
  const [state, setState] = useState<ReplayState>(() => emptyReplayState())
  const [open, setOpen] = useState(false)
  const [platform, setPlatform] = useState<Platform | null>(null)
  const [windowMs, setWindowMs] = useState<number | null>(null)
  const [warmup, setWarmup] = useState<string>('')
  const [latency, setLatency] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)

  useEffect(() => {
    if (!open) return
    let disposed = false
    const poll = (): void => {
      if (pending.current) return
      pending.current = true
      void window.quantScreenTrader.replay({ operation: 'state' })
        .then(next => { if (!disposed) setState(next) })
        .catch(() => {})
        .finally(() => { pending.current = false })
    }
    poll()
    const timer = window.setInterval(poll, POLL_MS)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [open])

  const running = state.status === 'RUNNING' || state.status === 'PENDING'
  const start = (): void => {
    setError('')
    const parsed = warmup.trim() === '' ? null : Number(warmup.trim())
    if (parsed !== null && (!Number.isFinite(parsed) || parsed < 0)) {
      setError('ช่วงอุ่นเครื่องต้องเป็นตัวเลขมิลลิวินาทีที่ไม่ติดลบ')
      return
    }
    void window.quantScreenTrader.replay({
      operation: 'start', platform, windowMs,
      warmupMs: parsed === null ? null : Math.round(parsed),
      latencyScenarios: latency ? LATENCY : []
    }).then(setState).catch(() => setError('เริ่มการจำลองไม่สำเร็จ'))
  }
  const cancel = (): void => {
    void window.quantScreenTrader.replay({ operation: 'cancel' })
      .then(setState).catch(() => setError('ยกเลิกไม่สำเร็จ'))
  }

  return <section className="analytics-panel" aria-label="replay backtest research">
    <h2>จำลองย้อนหลัง (Replay / Backtest)</h2>
    <p>{state.available ? `v${state.replayVersion}` : 'ต่อเอ็นจิ้นไม่ได้'}
      {state.message ? ` · ${state.message}` : ''}</p>
    <p role="note" className="analytics-notice">{REPLAY_NOT_APPLIED_NOTICE}</p>
    <details open={open} onToggle={event => setOpen((event.target as HTMLDetailsElement).open)}>
      <summary>เปิดแผงจำลองย้อนหลัง</summary>

      <div className="analytics-card">
        <h4>ตั้งค่าการจำลอง</h4>
        <label>ช่วงประวัติ <select value={String(windowMs)} disabled={running}
          onChange={event => setWindowMs(event.target.value === 'null' ? null : Number(event.target.value))}>
          {WINDOWS.map(option => <option key={option.label} value={String(option.value)}>{option.label}</option>)}
        </select></label>
        {' '}
        <label>โบรกเกอร์ <select value={String(platform)} disabled={running}
          onChange={event => setPlatform(event.target.value === 'null' ? null : event.target.value as Platform)}>
          {PLATFORMS.map(option => <option key={option.label} value={String(option.value)}>{option.label}</option>)}
        </select></label>
        {' '}
        <label>อุ่นเครื่อง (ms) <input value={warmup} disabled={running} inputMode="numeric"
          placeholder="ว่าง = คำนวณจาก qfe-v2" onChange={event => setWarmup(event.target.value)} /></label>
        {' '}
        <label><input type="checkbox" checked={latency} disabled={running}
          onChange={event => setLatency(event.target.checked)} /> ทดสอบความหน่วง</label>
        {' '}
        <button disabled={running || !state.available} onClick={start}>รันการจำลอง</button>
        {' '}
        <button disabled={!running} onClick={cancel}>ยกเลิก</button>
        {error && <p role="alert" className="error-banner">{error}</p>}
        <small>ว่างไว้แล้วระบบจะคำนวณช่วงอุ่นเครื่องจากสัญญาที่แช่แข็งไว้เอง
          {' '}— ห้าสิบแท่งของกรอบเวลาช้าสุดที่โบรกนั้นอ่าน ไม่ใช่ตัวเลขที่เดาเอา</small>
      </div>

      <div className="analytics-card">
        <h4>สถานะ</h4>
        <p>{state.status} · ขั้น {state.phase}
          {' · '}คืบหน้า {replayPercentLabel(state.percent)}
          {' · '}เหตุการณ์ {state.processedEvents.toLocaleString()} / {state.totalEvents.toLocaleString()}</p>
        <p>เวลาตลาดปัจจุบัน {stamp(state.currentMarketTime)}</p>
        {state.error && <p role="alert" className="error-banner">{state.error}</p>}
      </div>

      <ReplayResult state={state} />
    </details>
  </section>
}
