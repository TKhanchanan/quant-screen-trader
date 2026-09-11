import { useEffect, useState, type JSX } from 'react'
import { dailyPnlLabel, defaultSessionGuardSettings, nextResetLabel, progressPercent,
  sessionBlockLabel, sessionMoney, sessionStatusLabel,
  type SessionGuardSettings, type SessionGuardState } from '@quant-screen-trader/shared-types'

/**
 * Phase 9.5 daily session: what today has actually realized, and whether it may keep going.
 *
 * Deliberately separate from the execution controls below it, and worded so the two cannot be
 * confused. **Stop Session** ends the trading day and cannot be undone before the next reset;
 * **Disarm** only takes the executor's finger off the button. One is a limit, the other is a
 * switch.
 *
 * Every number here is realized. An open paper outcome that is probably going to win is worth
 * nothing until it settles, and a day with no configured simulated money says so rather than
 * showing ฿0 — which would read as break-even on a day nobody priced.
 */
export function DailySessionPanel({ onError }: { onError: (message: string) => void }): JSX.Element {
  const [state, setState] = useState<SessionGuardState | null>(null)
  const [draft, setDraft] = useState<SessionGuardSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmStop, setConfirmStop] = useState(false)
  useEffect(() => {
    let disposed = false
    const poll = (): void => { void window.quantScreenTrader.sessionGuard({ operation: 'state' })
      .then(s => { if (!disposed) { setState(s); setDraft(current => current ?? s.settings) } })
      .catch(() => {}) }
    poll()
    const timer = window.setInterval(poll, 2000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [])

  const run = (action: () => Promise<SessionGuardState>, failure: string): void => {
    setBusy(true)
    void action().then(next => { setState(next); setDraft(next.settings) })
      .catch((error: unknown) => onError(error instanceof Error ? error.message : failure))
      .finally(() => setBusy(false))
  }
  const session = state?.session ?? null
  const settings = draft ?? defaultSessionGuardSettings()
  const patch = (change: Partial<SessionGuardSettings>): void =>
    setDraft({ ...settings, ...change })
  const amount = (value: string): number | null => value.trim() === '' ? null : Number(value)

  return <section className="session-panel" aria-label="daily session guard">
    <h2>รอบวัน (Daily Session)</h2>
    <p className={session?.canOpenNewEntry === false ? 'session-stopped' : 'session-open'}>
      {sessionStatusLabel(session?.status ?? null)}
      {' · '}{state ? sessionBlockLabel(state) : 'กำลังอ่านสถานะ'}
      {state?.available === false ? ' · ต่อเอ็นจิ้นไม่ได้' : ''} · v{state?.sessionGuardVersion ?? '—'}</p>

    <dl className="session-grid">
      <div><dt>กำไร/ขาดทุนวันนี้ (รู้ผลแล้ว)</dt><dd>{state ? dailyPnlLabel(state) : '—'}</dd></div>
      <div><dt>เป้ากำไร</dt><dd>{session?.profitTarget === null || !session
        ? 'ไม่ได้ตั้ง' : sessionMoney(session.profitTarget, session.currency)}</dd></div>
      <div><dt>เหลืออีก</dt><dd>{state?.remainingToTarget === null || !state || !session
        ? '—' : sessionMoney(state.remainingToTarget, session.currency)}</dd></div>
      <div><dt>ขีดขาดทุน</dt><dd>{session?.lossLimit === null || !session
        ? 'ไม่ได้ตั้ง' : sessionMoney(-session.lossLimit, session.currency)}</dd></div>
    </dl>

    <p>ความคืบหน้าเป้ากำไร {progressPercent(state?.targetProgress ?? null)}
      <progress max={1} value={state?.targetProgress ?? 0} /></p>
    <p>ความคืบหน้าขีดขาดทุน {progressPercent(state?.lossProgress ?? null)}
      <progress className="loss" max={1} value={state?.lossProgress ?? 0} /></p>

    <p>รู้ผลแล้ว {session?.resolvedTrades ?? 0} ไม้ · ถูก {session?.wins ?? 0}
      {' '}· ผิด {session?.losses ?? 0} · เสมอ {session?.draws ?? 0}
      {' · '}ยังไม่รู้ผล {state?.openTrades ?? 0}
      {session?.monetaryTrades !== undefined && session.monetaryTrades < session.resolvedTrades
        ? ` · ไม่มีมูลค่าเงิน ${session.resolvedTrades - session.monetaryTrades}` : ''}</p>

    {session?.status === 'LOCKED_FOR_DAY' || session?.status === 'COMPLETED'
      ? <p role="status" className="session-locked">จบรอบวันแล้ว · รีเซ็ตรอบถัดไป {state ? nextResetLabel(state) : '—'}</p>
      : null}
    {state?.settingsError ? <p role="alert" className="error-banner">{state.settingsError}</p> : null}

    <div className="toolbar">
      <button className="stop-session" disabled={busy || session?.canOpenNewEntry === false}
        onClick={() => setConfirmStop(!confirmStop)}>หยุดรอบวันนี้…</button>
      {confirmStop && <>
        <span role="alert">จบรอบวันนี้ ไม่รับไม้ใหม่จนถึงรอบถัดไป — คนละอย่างกับการ Disarm</span>
        <button className="stop-session" disabled={busy} onClick={() => { setConfirmStop(false)
          run(() => window.quantScreenTrader.sessionGuard({ operation: 'stop' }), 'หยุดรอบวันไม่สำเร็จ') }}>
          ยืนยัน หยุดรอบวัน</button>
        <button disabled={busy} onClick={() => setConfirmStop(false)}>ยกเลิก</button>
      </>}
    </div>

    <details>
      <summary>ตั้งค่ารอบวัน</summary>
      <div className="toolbar">
        <label><input type="checkbox" checked={settings.enabled} disabled={busy}
          onChange={e => patch({ enabled: e.target.checked })} /> เปิดการคุมรอบวัน</label>
        <label>เป้ากำไร <input type="number" min={0} step={10} disabled={busy}
          value={settings.dailyProfitTarget ?? ''}
          onChange={e => patch({ dailyProfitTarget: amount(e.target.value) })} /></label>
        <label>ขีดขาดทุน <input type="number" min={0} step={10} disabled={busy}
          value={settings.dailyLossLimit ?? ''}
          onChange={e => patch({ dailyLossLimit: amount(e.target.value) })} /></label>
        <label>สกุลเงิน <input type="text" size={5} maxLength={8} disabled={busy}
          value={settings.currency} onChange={e => patch({ currency: e.target.value })} /></label>
      </div>
      <div className="toolbar">
        <label>เขตเวลา <input type="text" size={16} maxLength={64} disabled={busy}
          value={settings.timezone} onChange={e => patch({ timezone: e.target.value })} /></label>
        <label>เริ่มรอบวันเวลา <select value={settings.resetHour} disabled={busy}
          onChange={e => patch({ resetHour: Number(e.target.value) })}>
          {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>
            {String(hour).padStart(2, '0')}:00</option>)}</select></label>
      </div>
      <div className="toolbar">
        <label><input type="checkbox" checked={settings.notifyOnProfitTarget} disabled={busy}
          onChange={e => patch({ notifyOnProfitTarget: e.target.checked })} /> แจ้งเตือนเมื่อถึงเป้า</label>
        <label><input type="checkbox" checked={settings.notifyOnLossLimit} disabled={busy}
          onChange={e => patch({ notifyOnLossLimit: e.target.checked })} /> แจ้งเตือนเมื่อถึงขีดขาดทุน</label>
      </div>
      <div className="toolbar">
        <label><input type="checkbox" checked={settings.closeAppOnProfitTarget} disabled={busy}
          onChange={e => patch({ closeAppOnProfitTarget: e.target.checked })} /> ปิดโปรแกรมเมื่อถึงเป้า</label>
        <label><input type="checkbox" checked={settings.closeAppOnLossLimit} disabled={busy}
          onChange={e => patch({ closeAppOnLossLimit: e.target.checked })} /> ปิดโปรแกรมเมื่อถึงขีดขาดทุน</label>
        <label><input type="checkbox" checked={settings.waitForOpenTradesBeforeClose} disabled={busy}
          onChange={e => patch({ waitForOpenTradesBeforeClose: e.target.checked })} /> รอไม้ที่ค้างรู้ผลก่อนปิด</label>
      </div>
      <button className="primary-button" disabled={busy} onClick={() => run(() =>
        window.quantScreenTrader.sessionGuard({ operation: 'settings', settings }),
        'บันทึกค่ารอบวันไม่สำเร็จ')}>บันทึก</button>
      <small>เป้ากำไรกับขีดขาดทุนเป็นเงื่อนไข “หยุด” เท่านั้น ไม่ได้ไปเปลี่ยนคะแนน ไม่ได้ลดเกณฑ์คัดเลือก
        และไม่มีการเพิ่มเงินลงทุนหลังแพ้ · ตัวเลขทั้งหมดคิดจากไม้ที่รู้ผลแล้วเท่านั้น</small>
    </details>
  </section>
}
