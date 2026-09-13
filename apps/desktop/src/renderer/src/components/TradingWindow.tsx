import { useEffect, useState, type JSX } from 'react'
import { boardLabel, candidateLabel, leadLabel, type OpportunityState, type Platform } from '@quant-screen-trader/shared-types'
import { PLATFORM_DETAILS } from '../platforms'
import { AnalyticsPanel } from './AnalyticsPanel'
import { DailySessionPanel } from './DailySessionPanel'
import { PaperPanel } from './PaperPanel'
import { ReplayPanel } from './ReplayPanel'
import { PolicyPanel } from './PolicyPanel'
import { TradingControls } from './TradingControls'

/**
 * The board and the execution controls, in their own window.
 *
 * They were originally stacked above the embedded browser in the workspace, which left the nine
 * charts a few hundred pixels of height — too little for the grid detector to separate the cells,
 * and far too little for the price ROI to be legible. Here they cost the charts nothing.
 */
export function TradingWindow({ platform }: { platform: Platform }): JSX.Element {
  const details = PLATFORM_DETAILS[platform]
  const [opportunity, setOpportunity] = useState<OpportunityState | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let disposed = false
    const poll = (): void => { void window.quantScreenTrader.opportunities(platform)
      .then(s => { if (!disposed) setOpportunity(s) }).catch(() => {}) }
    poll()
    const timer = window.setInterval(poll, 2000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [platform])
  const board = opportunity?.board ?? null
  return <main className="trading-shell">
    <h1>{details.name} — แผงเทรด</h1>
    {error && <p role="alert" className="error-banner">{error}</p>}
    <section className="opportunity-board" aria-label={`${details.name} opportunity board`}>
      <h2>บอร์ดสัญญาณ</h2>
      <p>{boardLabel(board)} · {board ? `${board.receivedSlots}/${board.expectedSlots} ช่อง` : '—'} · ปิดแท่ง {board?.primaryTimeframe ?? '—'}
        {board?.missingSlots.length ? ` · ขาดช่อง ${board.missingSlots.join(', ')}` : ''}
        {opportunity?.available === false ? ' · ต่อเอ็นจิ้นไม่ได้' : ''} · v{opportunity?.rankingVersion ?? '—'}</p>
      {board?.watchlist.length
        ? <ol>{board.watchlist.map(entry => <li key={entry.slotId}>{candidateLabel(entry)}</li>)}</ol>
        : <p>รอบนี้ไม่มีตัวเลือกที่มีทิศทาง</p>}
      <p>{leadLabel(board)}</p>
      <small>เป็นผลวิเคราะห์ ไม่ใช่คำสั่ง คะแนนใช้เรียงลำดับตลาดที่กำลังดูอยู่ ไม่ใช่ความน่าจะเป็นที่จะชนะ</small>
    </section>
    <PaperPanel platform={platform} />
    <DailySessionPanel onError={setError} />
    <TradingControls platform={platform} onError={setError} />
    <AnalyticsPanel platform={platform} />
    <ReplayPanel />
    <PolicyPanel />
    <p className="trading-hint">ปุ่ม Sync Assets, Calibrate Chart Area และ Start observation ยังอยู่ในหน้าต่าง
      {' '}{details.name} workspace — เปิดหน้าต่างนั้นให้ใหญ่ไว้ ตัวตรวจตารางต้องเห็นกราฟทั้งเก้าในขนาดที่อ่านออก</p>
  </main>
}
