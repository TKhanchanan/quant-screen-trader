import { useEffect, useState, type JSX } from 'react'
import { paperMoneyLabel, paperOpenLine, paperResultLine, paperStatsLine,
  type PaperState, type Platform } from '@quant-screen-trader/shared-types'

/**
 * Phase 9 diagnostics: what the market did after each Phase 8 selection.
 *
 * Deliberately its own panel, above the execution controls and worded differently from them.
 * An order ticket saying "สำเร็จ (แผงตอบสนอง)" means the broker panel visibly reacted to a
 * press. A paper trade saying "ทิศทางถูก" means the market moved the way the analysis said it
 * would, with no order, no press and no money. Showing them in one list would invite reading
 * a confirmed press as a correct call, which it is not.
 *
 * Simulated money appears only when an operator has configured a stake and payout rate. With
 * none configured the panel says so rather than showing a zero.
 */
export function PaperPanel({ platform }: { platform: Platform }): JSX.Element {
  const [paper, setPaper] = useState<PaperState | null>(null)
  useEffect(() => {
    let disposed = false
    const poll = (): void => { void window.quantScreenTrader.paper(platform)
      .then(s => { if (!disposed) setPaper(s) }).catch(() => {}) }
    poll()
    const timer = window.setInterval(poll, 2000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [platform])
  const stats = paper?.stats ?? null
  const money = stats?.netPaperPnl ?? null
  return <section className="paper-panel" aria-label={`${platform} paper simulation`}>
    <h2>ผลจำลอง (Paper)</h2>
    <p>{paper?.available === false ? 'ต่อเอ็นจิ้นไม่ได้' : paper?.enabled === false
      ? 'ปิดการจำลองอยู่' : 'กำลังวัดผลสัญญาณที่ผ่านเกณฑ์'} · v{paper?.paperVersion ?? '—'}</p>
    <h3>กำลังวัดผล ({paper?.open.length ?? 0})</h3>
    {paper?.open.length
      ? <ol>{paper.open.map(trade => <li key={trade.paperTradeId}>{paperOpenLine(trade)}</li>)}</ol>
      : <p>ยังไม่มีไม้จำลองที่กำลังวัดผล</p>}
    <h3>ผลล่าสุด</h3>
    {paper?.recent.length
      ? <ol>{paper.recent.map(trade => <li key={trade.paperTradeId}>{paperResultLine(trade)}</li>)}</ol>
      : <p>ยังไม่มีผล</p>}
    <p className="paper-tally">{paperStatsLine(stats)}</p>
    <p>กำไร/ขาดทุนจำลอง: {paper?.accountingConfigured
      ? paperMoneyLabel(money, 'THB') : 'ยังไม่ได้ตั้งค่าเงินจำลอง'}</p>
    <small>เป็นการวัดผลย้อนหลังของสัญญาณ ไม่ใช่ออเดอร์จริง ไม่ได้กดปุ่มโบรก และไม่ใช่เงินจริง
      {' '}— คนละเรื่องกับสถานะออเดอร์ในแผงด้านล่าง ตัวเลขเป็นการบรรยายสิ่งที่บันทึกไว้เท่านั้น</small>
  </section>
}
