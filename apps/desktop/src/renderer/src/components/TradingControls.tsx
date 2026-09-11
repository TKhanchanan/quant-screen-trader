import { useEffect, useState, type JSX } from 'react'
import { blockLabel, ticketLabel, type ExecutionMode, type ExecutionSettings, type ExecutionState,
  type OrderDirection, type Platform } from '@quant-screen-trader/shared-types'

/**
 * The operator's view of the execution layer. Thai, like the broker panel it sits beside.
 *
 * STOP is deliberately always enabled and never waits on a request in flight: it is the control
 * an operator reaches for when something is wrong, so nothing about the rest of this panel's
 * state may stand between it and disarming.
 */
export function TradingControls({ platform, onError }: {
  platform: Platform; onError: (message: string) => void
}): JSX.Element {
  const [state, setState] = useState<ExecutionState | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmTest, setConfirmTest] = useState(false)
  useEffect(() => {
    let disposed = false
    const poll = (): void => { void window.quantScreenTrader.execution({ operation: 'state', platform })
      .then(s => { if (!disposed) setState(s) }).catch(() => {}) }
    poll()
    const timer = window.setInterval(poll, 1000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [platform])

  const run = (action: () => Promise<ExecutionState>, failure: string): void => {
    setBusy(true)
    void action().then(setState).catch((error: unknown) =>
      onError(error instanceof Error ? error.message : failure)).finally(() => setBusy(false))
  }
  const patch = (change: Partial<ExecutionSettings>): void => {
    if (!state) return
    run(() => window.quantScreenTrader.execution({ operation: 'settings', platform,
      settings: { ...state.settings, ...change } }), 'ตั้งค่าไม่สำเร็จ')
  }
  const limits = (change: Partial<ExecutionSettings['limits']>): void => {
    if (!state) return
    patch({ limits: { ...state.settings.limits, ...change } })
  }
  const measured = state?.controls?.slots.length ?? 0
  const weakest = state?.controls?.slots.length
    ? Math.min(...state.controls.slots.map(slot => slot.confidence)) : null

  return <section className="trading-controls" aria-label={`${platform} execution`}>
    <h2>เข้าออเดอร์อัตโนมัติ</h2>
    <ol className="trading-steps">
      <li>ตั้งเงินลงทุนกับเวลาหมดอายุบนแผงโบรกเองให้ครบทุกช่องก่อน</li>
      <li>กด <b>วัดตำแหน่งปุ่ม</b> แล้วดูว่าได้ครบ 9 ช่องไหม</li>
      <li>เลือกโหมด <b>AUTO</b> แล้วกด <b>Arm</b> — จากนั้นมีสัญญาณเมื่อไหร่มันกดให้เอง</li>
    </ol>
    <div className="toolbar">
      <label>โหมด <select value={state?.settings.mode ?? 'OFF'} disabled={busy || !state}
        onChange={e => patch({ mode: e.target.value as ExecutionMode })}>
        <option value="OFF">OFF — ไม่ส่งอะไรเลย</option>
        <option value="PAPER">PAPER — คิดแต่ไม่กดจริง</option>
        <option value="AUTO">AUTO — กดปุ่มโบรกจริง</option>
      </select></label>
      <button disabled={busy || !state} onClick={() => run(() =>
        window.quantScreenTrader.execution({ operation: 'calibrateControls', platform }),
        'วัดตำแหน่งปุ่มไม่สำเร็จ')}>วัดตำแหน่งปุ่ม</button>
      <label>ปุ่มสีเขียวคือ <select value={state?.controls?.directionForGreen ?? 'HIGHER'}
        disabled={busy || !state?.controls}
        onChange={e => run(() => window.quantScreenTrader.execution({ operation: 'directionForGreen',
          platform, direction: e.target.value as OrderDirection }), 'สลับทิศทางไม่สำเร็จ')}>
        <option value="HIGHER">ขึ้น / ซื้อ</option>
        <option value="LOWER">ลง / ขาย</option>
      </select></label>
      <button disabled={busy || state?.armed || state?.settings.mode !== 'AUTO'} onClick={() => run(() =>
        window.quantScreenTrader.execution({ operation: 'arm', platform }), 'Arm ไม่สำเร็จ')}>Arm</button>
      <button className="stop-execution" onClick={() => run(() =>
        window.quantScreenTrader.execution({ operation: 'disarm', platform }), 'หยุดไม่สำเร็จ — ปิดหน้าต่างนี้')}>
        หยุด</button>
      <strong>{state?.armed ? 'พร้อมยิง (ARMED)' : 'ยังไม่พร้อม'}</strong>
    </div>
    <div className="toolbar">
      <label>คะแนนขั้นต่ำ <input type="number" min={0} max={1} step={.05} disabled={busy || !state}
        value={state?.settings.limits.minRankScore ?? 0}
        onChange={e => limits({ minRankScore: Number(e.target.value) })} /></label>
      <label>ความมั่นใจขั้นต่ำ <input type="number" min={0} max={1} step={.05} disabled={busy || !state}
        value={state?.settings.limits.minEnsembleConfidence ?? 0}
        onChange={e => limits({ minEnsembleConfidence: Number(e.target.value) })} /></label>
      <label>พักระหว่างไม้ <select value={state?.settings.limits.cooldownMs ?? 60000} disabled={busy || !state}
        onChange={e => limits({ cooldownMs: Number(e.target.value) })}>
        {[5000, 15000, 30000, 60000, 300000].map(ms => <option key={ms} value={ms}>{ms / 1000} วิ</option>)}</select></label>
      <label>ไม้สูงสุด / ชม. <input type="number" min={0} max={240} step={1} disabled={busy || !state}
        value={state?.settings.limits.maxOrdersPerHour ?? 0}
        onChange={e => limits({ maxOrdersPerHour: Number(e.target.value) })} /></label>
      <label><input type="checkbox" disabled={busy || !state}
        checked={state?.settings.limits.acceptBoardStatus.includes('PARTIAL') ?? false}
        onChange={e => limits({ acceptBoardStatus: e.target.checked ? ['READY', 'PARTIAL'] : ['READY'] })} />
        ยิงตอนข้อมูลไม่ครบ (PARTIAL) ด้วย</label>
    </div>
    <div className="toolbar">
      <button disabled={busy || !state?.controlsValid || !measured} onClick={() => setConfirmTest(!confirmTest)}>
        {measured ? `ทดสอบกดครบ ${measured * 2} ปุ่ม…` : 'ทดสอบกดทุกปุ่ม — ต้องวัดตำแหน่งปุ่มก่อน'}</button>
      {confirmTest && <>
        <span role="alert">กดทุกปุ่มอย่างละครั้ง ทั้งขึ้นและลง โดยใช้เงินลงทุนกับเวลาหมดอายุที่ตั้งไว้บนแผงโบรก
          — เป็นออเดอร์จริงทุกไม้</span>
        <button className="stop-execution" disabled={busy} onClick={() => { setConfirmTest(false); run(() =>
          window.quantScreenTrader.execution({ operation: 'testControls', platform,
            confirm: 'PRESS ALL CONTROLS' }), 'เริ่มทดสอบไม่สำเร็จ') }}>
          ยืนยัน กดทั้ง {measured * 2} ปุ่ม</button>
        <button disabled={busy} onClick={() => setConfirmTest(false)}>ยกเลิก</button>
      </>}
    </div>
    <p>วัดตำแหน่งปุ่มได้ {measured}/9 ช่อง{weakest === null ? '' : ` · ต่ำสุด ${weakest.toFixed(2)}`}
      {state?.controlsValid === false && measured ? ' · เก่าแล้ว ต้องวัดใหม่' : ''}
      {state?.controls?.reasons.length ? ` · ${state.controls.reasons.join(', ')}` : ''}</p>
    <p>ออเดอร์ชั่วโมงนี้ {state?.ordersLastHour ?? 0}/{state?.settings.limits.maxOrdersPerHour ?? 0}
      {' · '}ยืนยันไม่ได้ติดกัน {state?.unverifiedInARow ?? 0}
      {' · '}บอร์ดล่าสุด {state?.lastBoardAsOf ? new Date(state.lastBoardAsOf).toLocaleTimeString('th-TH') : '—'}
      {' · '}v{state?.executionVersion ?? '—'}</p>
    {state?.blocked.length
      ? <p role="status" className="blocked-list">ติดอยู่ที่: {state.blocked.map(blockLabel).join(' · ')}</p>
      : <p role="status" className="ready-line">พร้อมแล้ว — บอร์ดถัดไปที่ผ่านเกณฑ์จะถูกกดทันที</p>}
    <details open><summary>รายการออเดอร์ ({state?.tickets.length ?? 0})</summary>
      {state?.tickets.length
        ? <ol>{state.tickets.map(ticket => <li key={ticket.id}>{ticketLabel(ticket)}</li>)}</ol>
        : <p>ยังไม่มีรายการ</p>}
    </details>
    <small>โหมด AUTO กดปุ่มของโบรกด้วยเงินจริง ต้องวัดตำแหน่งปุ่มใหม่ทุกครั้งที่เปลี่ยน zoom
      ย่อขยายหน้าต่าง หรือโหลดแพลตฟอร์มใหม่ และตรวจว่าสีที่แมปไว้ตรงกับป้ายของโบรกจริง</small>
  </section>
}
