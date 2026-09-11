import { useEffect, useRef, useState, type JSX } from 'react'
import { NOT_APPLIED_NOTICE, binLine, calibrationVerdict, emptyAnalyticsState, moneyLine,
  outcomeLine, percentLabel, sampleNote, segmentLine, stabilityLabel, thresholdLine, warningLabel,
  winRateLabel, type AnalyticsState, type Calibration, type MatrixCell, type Platform,
  type SegmentMetrics } from '@quant-screen-trader/shared-types'

const POLL_MS = 15_000
/**
 * Slower than every other panel here, on purpose. Phase 10 reads the whole durable record and
 * an answer built from a thousand outcomes does not change in two seconds; polling it at the
 * cadence of a live board would spend the machine's time recomputing yesterday.
 */

const MIN_ROWS = 3

function bestAndWorst(assets: SegmentMetrics[]): { best: SegmentMetrics[]; worst: SegmentMetrics[] } {
  // Only assets that cleared their own sample floor may be placed in an ordering. A 100% rate
  // over four trades is not the best asset, it is four trades.
  const ranked = assets.filter(asset => asset.rankable && asset.outcomes.winRateExcludingDraws !== null)
    .sort((a, b) => (b.outcomes.winRateExcludingDraws ?? 0) - (a.outcomes.winRateExcludingDraws ?? 0))
  return { best: ranked.slice(0, MIN_ROWS), worst: ranked.slice(-MIN_ROWS).reverse() }
}

function Curve({ title, calibration }: { title: string; calibration: Calibration | null }): JSX.Element {
  const populated = calibration?.bins.filter(bin => bin.outcomes.resolved > 0) ?? []
  return <div className="analytics-card">
    <h4>{title}</h4>
    {populated.length
      ? <ol className="analytics-bins">{populated.map(bin =>
          <li key={bin.index} className={bin.outcomes.sampleLabel === 'OK' ? '' : 'low-sample'}>
            {binLine(bin)}</li>)}</ol>
      : <p>ยังไม่มีไม้ที่รู้ผลในช่วงคะแนนใด</p>}
    <p className="analytics-verdict">{calibrationVerdict(calibration)}</p>
    {calibration?.warnings.map(code => <p key={code} role="status" className="analytics-warning">
      {warningLabel(code)}</p>)}
    <small>เป็น “เส้นผลลัพธ์จริงตามช่วงคะแนน” ไม่ใช่ความน่าจะเป็นที่คาลิเบรตแล้ว
      {' '}— คะแนนพวกนี้ถูกสร้างตอนที่ยังไม่เคยเห็นผลลัพธ์เลย</small>
  </div>
}

/**
 * Every research table, as a pure function of one analysis.
 *
 * Separated from the polling shell so it can be rendered against a known analysis in a test.
 * A panel whose numbers are only ever exercised against an empty state is a panel whose
 * formatting nobody has checked.
 */
export function AnalyticsTables({ state }: { state: AnalyticsState }): JSX.Element {
  const { best, worst } = bestAndWorst(state.assets)
  const cells = state.strategyRegime?.cells ?? []
  const cellFor = (row: string, column: string): MatrixCell | undefined =>
    cells.find(cell => cell.row === row && cell.column === column)
  return <>
      {state.warnings.map(code => <p key={code} role="status" className="analytics-warning">
        {warningLabel(code)}</p>)}

      <div className="analytics-card">
        <h4>ภาพรวม</h4>
        <p>{outcomeLine(state.overall)}</p>
        <p>กำไร/ขาดทุนจำลอง: {moneyLine(state.money)}</p>
        <p>{state.quality
          ? `บันทึกไว้ ${state.quality.totalTrades} ไม้ · รู้ผล ${state.quality.resolved}` +
            ` · ยังไม่รู้ผล ${state.quality.pendingEntry + state.quality.open}` +
            ` · ยกเลิก ${state.quality.cancelled} · วัดไม่ได้ ${state.quality.invalid}` +
            ` · สัดส่วนที่รู้ผล ${percentLabel(state.quality.resolvedRate)}`
          : 'ยังไม่มีรายงานคุณภาพข้อมูล'}</p>
        <p>{state.split
          ? `แบ่งตามเวลา (ไม่สุ่ม): ฝึก ${state.split.train} · ตรวจ ${state.split.validation}` +
            ` · ทดสอบ ${state.split.test}`
          : 'ยังแบ่งช่วงเวลาไม่ได้'}</p>
      </div>

      <Curve title="คะแนน rankScore เทียบผลจริง" calibration={state.rank} />
      <Curve title="ความมั่นใจ ensembleConfidence เทียบผลจริง" calibration={state.confidence} />

      <div className="analytics-card">
        <h4>ตามสภาพตลาด (Regime)</h4>
        {state.regimes.length
          ? <table className="analytics-table"><thead><tr>
              <th>Regime</th><th>N</th><th>สัดส่วนถูก</th><th>คะแนนเฉลี่ย</th><th>เงินจำลอง</th>
            </tr></thead><tbody>
              {state.regimes.map(regime => <tr key={regime.key}
                className={regime.rankable ? '' : 'low-sample'}>
                <td>{regime.key}</td>
                <td>{regime.outcomes.resolved}</td>
                <td>{winRateLabel(regime.outcomes)}</td>
                <td>{regime.averageRankScore === null ? '—' : regime.averageRankScore.toFixed(2)}</td>
                <td>{regime.money.available && regime.money.netPaperPnl !== null
                  ? regime.money.netPaperPnl.toFixed(2) : '—'}</td>
              </tr>)}
            </tbody></table>
          : <p>ยังไม่มีผลแยกตามสภาพตลาด</p>}
      </div>

      <div className="analytics-card">
        <h4>กลยุทธ์ × สภาพตลาด</h4>
        {state.strategyRegime && state.strategyRegime.rows.length
          ? <table className="analytics-table"><thead><tr><th>กลยุทธ์</th>
              {state.strategyRegime.columns.map(column => <th key={column}>{column}</th>)}
            </tr></thead><tbody>
              {state.strategyRegime.rows.map(row => <tr key={row}><th scope="row">{row}</th>
                {state.strategyRegime?.columns.map(column => {
                  const cell = cellFor(row, column)
                  return <td key={column} className={cell?.sampleLabel === 'OK' ? '' : 'low-sample'}>
                    {cell && cell.agreed > 0
                      ? `${cell.agreed} / ${percentLabel(cell.outcomes.winRateExcludingDraws)}`
                      : '—'}</td>
                })}
              </tr>)}
            </tbody></table>
          : <p>ยังไม่มีคะแนนโหวตรายกลยุทธ์ที่จับคู่กับผลได้</p>}
        <small>ตัวเลขในช่องคือ “จำนวนไม้ที่กลยุทธ์นั้นเห็นตรงกับทิศทางที่เลือก / สัดส่วนถูก”
          {' '}ช่องที่ตัวอย่างน้อยจะจางไว้ และไม่ควรใช้สรุป</small>
      </div>

      <div className="analytics-card">
        <h4>ตามสินทรัพย์</h4>
        {best.length
          ? <><p>ผลดีที่สุด (เฉพาะที่ตัวอย่างพอ)</p>
              <ol>{best.map(asset => <li key={asset.key}>{segmentLine(asset)}</li>)}</ol>
              <p>ผลแย่ที่สุด</p>
              <ol>{worst.map(asset => <li key={asset.key}>{segmentLine(asset)}</li>)}</ol></>
          : <p>ยังไม่มีสินทรัพย์ไหนมีตัวอย่างพอจะจัดอันดับ</p>}
        <small>เป็นการบรรยายสิ่งที่บันทึกไว้ ไม่ใช่รายการอนุญาต/ห้ามเทรด</small>
      </div>

      <div className="analytics-card">
        <h4>ตามชั่วโมง ({state.timezone})</h4>
        {state.hours.length
          ? <table className="analytics-table"><thead><tr>
              <th>ชั่วโมง</th><th>N</th><th>สัดส่วนถูก</th><th>เงินจำลอง</th>
            </tr></thead><tbody>
              {state.hours.map(hour => <tr key={hour.key} className={hour.rankable ? '' : 'low-sample'}>
                <td>{hour.key}:00</td>
                <td>{hour.outcomes.resolved}</td>
                <td>{percentLabel(hour.outcomes.winRateExcludingDraws)}</td>
                <td>{hour.money.available && hour.money.netPaperPnl !== null
                  ? hour.money.netPaperPnl.toFixed(2) : '—'}</td>
              </tr>)}
            </tbody></table>
          : <p>ยังไม่มีผลแยกตามชั่วโมง</p>}
      </div>

      <div className="analytics-card analytics-research">
        <h4>เกณฑ์ที่ค้นเจอ (งานวิจัยเท่านั้น)</h4>
        {state.thresholds.length
          ? <ol>{state.thresholds.map(candidate =>
              <li key={`${candidate.metric}-${candidate.threshold}`}
                className={candidate.stable ? 'threshold-stable' : 'threshold-unstable'}>
                {thresholdLine(candidate)}
                <small> · {stabilityLabel(candidate.stability)}
                  {candidate.reasons.length ? ` · ${candidate.reasons.join(', ')}` : ''}</small>
              </li>)}</ol>
          : <p>ยังไม่มีเกณฑ์ที่ผ่านเงื่อนไขตัวอย่างขั้นต่ำ</p>}
        <p role="alert" className="analytics-notice">{NOT_APPLIED_NOTICE}</p>
        <small>ค้นจากช่วง “ฝึก” เท่านั้น แล้วเอาไปวัดกับช่วง “ตรวจ” และ “ทดสอบ”
          {' '}ตัวที่ไม่นิ่งข้ามช่วงจะถูกทำเครื่องหมายไว้ และไม่ใช่ข้อเสนอแนะ</small>
      </div>

      <small className="analytics-footer">
        ทั้งหมดนี้เป็นการวัดผลย้อนหลัง ไม่ได้เปลี่ยนคะแนน ไม่ได้เปลี่ยนเกณฑ์คัดเลือก
        {' '}ไม่ได้แตะรอบวัน และไม่ได้แตะการส่งคำสั่ง
        {sampleNote(state.sampleLabel) ? ` · ${sampleNote(state.sampleLabel)}` : ''}
      </small>
  </>
}


/**
 * Phase 10 research: did the scores actually correspond to better outcomes?
 *
 * Deliberately the last panel in the window and deliberately collapsed, because it is the one
 * thing here nobody should act on in the moment. Everything above it describes what is
 * happening now; this describes what already happened, and the distance between those two is
 * the whole reason the panel carries a warning instead of a button.
 *
 * There is no "apply threshold" control anywhere in this file, and there is no bridge method
 * that would accept one. A candidate threshold is an observation about recorded history; wiring
 * it into live behaviour is a later phase's decision, and until that decision is taken the
 * honest thing for this panel to be is unable to act.
 */
export function AnalyticsPanel({ platform }: { platform: Platform }): JSX.Element {
  const [state, setState] = useState<AnalyticsState>(() => emptyAnalyticsState(platform))
  const [open, setOpen] = useState(false)
  const pending = useRef(false)
  useEffect(() => {
    if (!open) return
    let disposed = false
    const poll = (): void => {
      // A rebuild can outlast the interval, and stacking reads would queue rebuilds behind each
      // other rather than producing an answer any sooner.
      if (pending.current) return
      pending.current = true
      void window.quantScreenTrader.analytics(platform)
        .then(next => { if (!disposed) setState(next) })
        .catch(() => {})
        .finally(() => { pending.current = false })
    }
    poll()
    const timer = window.setInterval(poll, POLL_MS)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [platform, open])

  return <section className="analytics-panel" aria-label={`${platform} outcome analytics`}>
    <h2>วิเคราะห์ผลย้อนหลัง (Analytics / Calibration)</h2>
    <p>{state.available === false
      ? (state.busy ? 'เอ็นจิ้นกำลังประมวลผลอยู่ — เดี๋ยวลองใหม่' : 'ยังไม่ได้อ่านผลวิเคราะห์')
      : `${platform === 'capitalbear' ? 'CapitalBear S5' : 'IQ Option M1'} · ` +
        `รู้ผลแล้ว ${state.sampleCount} ไม้ · เขตเวลา ${state.timezone}`}
      {' · '}v{state.analyticsVersion}</p>
    <p role="note" className="analytics-notice">{NOT_APPLIED_NOTICE}</p>
    <details open={open} onToggle={event => setOpen((event.target as HTMLDetailsElement).open)}>
      <summary>เปิดดูผลวิเคราะห์</summary>
      <AnalyticsTables state={state} />
    </details>
  </section>
}
