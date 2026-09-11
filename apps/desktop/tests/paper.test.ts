import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { PaperPanel } from '../src/renderer/src/components/PaperPanel'
import {
  IPC_CHANNELS, PaperStateSchema, PaperStatsSchema, PaperTradeSchema,
  paperMoneyLabel, paperOpenLine, paperResultLine, paperStatsLine,
  ticketLabel, OrderTicketSchema, type PaperTrade
} from '@quant-screen-trader/shared-types'

const trade = {
  paperTradeId: '5f0d2a9e-6c1b-5a4d-8e2f-0a1b2c3d4e5f', platform: 'capitalbear', slotId: 4,
  assetName: 'EUR/USD OTC', direction: 'UP', boardAsOf: 1_788_873_600_000,
  decisionAvailableAt: 1_788_873_601_200, rank: 1, rankScore: 0.57, ensembleConfidence: 0.62,
  durationMs: 5_000, entryTime: 1_788_873_602_000, entryPrice: 1.0842,
  expiryTargetTime: 1_788_873_607_000, expiryTime: 1_788_873_607_100, expiryPrice: 1.08451,
  priceDeltaBps: 2.86, status: 'RESOLVED', outcome: 'WIN', paperCurrency: null,
  paperStake: null, realizedPaperPnl: null, paperVersion: 'qst-paper-v1',
  reasons: ['BOARD_SELECTION', 'ENTRY_FILLED', 'EXPIRY_FILLED'], invalidReasons: []
}
const parsed = (changes: Record<string, unknown> = {}): PaperTrade =>
  PaperTradeSchema.parse({ ...trade, ...changes })
const stats = PaperStatsSchema.parse({
  resolved: 31, wins: 18, losses: 11, draws: 2, invalid: 1, cancelled: 0,
  winRateExcludingDraws: 18 / 29, winRateIncludingDraws: 18 / 31, currentWinStreak: 3,
  currentLossStreak: 0, averagePriceDeltaBps: 1.4, netPaperPnl: null, paperVersion: 'qst-paper-v1'
})
const source = (file: string): string =>
  readFileSync(join(import.meta.dirname, '..', file), 'utf8')

describe('paper simulation bridge', () => {
  it('accepts an engine trade and tolerates fields the desktop does not render', () => {
    const state = PaperStateSchema.parse({
      paperVersion: 'qst-paper-v1', available: true, enabled: true, accountingConfigured: false,
      open: [], recent: [{ ...trade, primaryRegime: 'TREND_UP', agreement: 0.7 }], stats
    })
    expect(state.recent[0]!.outcome).toBe('WIN')
    expect(IPC_CHANNELS.paper).toBe('paper:state')
  })

  it('rejects an unreadable trade rather than inventing an outcome', () => {
    expect(PaperTradeSchema.safeParse({ ...trade, outcome: 'CONFIRMED' }).success).toBe(false)
    expect(PaperTradeSchema.safeParse({ ...trade, direction: 'NEUTRAL' }).success).toBe(false)
    expect(PaperTradeSchema.safeParse({ ...trade, status: 'SENT' }).success).toBe(false)
    expect(PaperTradeSchema.safeParse({ ...trade, entryPrice: 0 }).success).toBe(false)
    expect(PaperTradeSchema.safeParse({ ...trade, rankScore: 1.4 }).success).toBe(false)
  })

  it('reports an unreachable engine without fabricating a result', () => {
    const offline = PaperStateSchema.parse({
      paperVersion: 'unknown', available: false, enabled: false, accountingConfigured: false,
      open: [], recent: [], stats: null
    })
    expect(offline.stats).toBeNull()
    expect(paperStatsLine(offline.stats)).toBe('ยังไม่มีผลจำลอง')
  })

  it('never renders an unconfigured simulated result as zero', () => {
    // Null means "not known". Rendering it as 0 would read as break-even, which is a claim.
    expect(paperMoneyLabel(null, null)).toBe('ยังไม่ได้ตั้งค่าเงินจำลอง')
    expect(paperMoneyLabel(null, 'THB')).toBe('ยังไม่ได้ตั้งค่าเงินจำลอง')
    expect(paperMoneyLabel(41, 'THB')).toBe('+฿41.00')
    expect(paperMoneyLabel(-50, 'THB')).toBe('−฿50.00')
    expect(paperMoneyLabel(0, 'THB')).toBe('฿0.00')
    expect(paperResultLine(parsed())).not.toContain('฿')
    expect(paperResultLine(parsed({ realizedPaperPnl: 41, paperCurrency: 'THB' })))
      .toContain('+฿41.00')
  })

  it('describes a live trade without promising anything about it', () => {
    const pending = paperOpenLine(parsed({
      status: 'PENDING_ENTRY', outcome: 'UNRESOLVED', entryTime: null, entryPrice: null,
      expiryTargetTime: null, expiryTime: null, expiryPrice: null, priceDeltaBps: null
    }))
    expect(pending).toContain('รอราคาแรกหลังมีสัญญาณ')
    expect(pending).toContain('ยังไม่ได้ราคาเข้า')
    expect(paperOpenLine(parsed({ status: 'OPEN', outcome: 'UNRESOLVED' })))
      .toContain('กำลังจับเวลา')
  })

  it('says a measurement could not be made instead of scoring it as a loss', () => {
    const line = paperResultLine(parsed({
      status: 'INVALID', outcome: 'INVALID', expiryTime: null, expiryPrice: null,
      priceDeltaBps: null, invalidReasons: ['RESOLUTION_TIMEOUT']
    }))
    expect(line).toContain('วัดผลไม่ได้')
    expect(line).toContain('RESOLUTION_TIMEOUT')
    expect(line).not.toContain('ทิศทางผิด')
  })

  it('keeps a paper outcome and an order ticket in different vocabularies', () => {
    // T-AQ. CONFIRMED means the broker panel reacted to a press. WIN means the market moved
    // the way the analysis said. A reader must never be able to take one for the other.
    const ticket = ticketLabel(OrderTicketSchema.parse({
      id: 'a', platform: 'capitalbear', slotId: 4, assetName: 'EUR/USD OTC', direction: 'HIGHER',
      boardAsOf: 1_788_873_600_000, rankScore: 0.57, ensembleConfidence: 0.62,
      state: 'CONFIRMED', reasons: [], requestedAt: '2026-09-10T04:00:02.000Z',
      pressedAt: '2026-09-10T04:00:02.120Z', latencyMs: 120
    }))
    const paper = paperResultLine(parsed())
    expect(ticket).toContain('สำเร็จ (แผงตอบสนอง)')
    expect(paper).toContain('ทิศทางถูก')
    expect(paper).not.toContain('สำเร็จ')
    expect(ticket).not.toContain('ทิศทางถูก')
    for (const word of ['CONFIRMED', 'SENT', 'ARMED', 'ORDER'])
      expect(paper.toUpperCase()).not.toContain(word)
  })

  it('states a tally without claiming it predicts anything', () => {
    const line = paperStatsLine(stats)
    expect(line).toContain('ถูก 18')
    expect(line).toContain('ผิด 11')
    expect(line).toContain('เสมอ 2')
    expect(line).toContain('62.1%')
    expect(line).not.toContain('฿')
    for (const claim of ['แม่น', 'กำไรแน่', 'การันตี', 'ความน่าจะเป็น'])
      expect(line).not.toContain(claim)
  })

  it('exposes reading paper outcomes and no way to act on one', () => {
    expect(Object.keys(IPC_CHANNELS)).toContain('paper')
    for (const channel of Object.values(IPC_CHANNELS))
      expect(channel).not.toMatch(/order|trade|stake|execute/i)
    expect(Object.keys(parsed())).not.toContain('ticketId')
    expect(Object.keys(parsed())).not.toContain('pressedAt')
  })
})

describe('execution isolation', () => {
  it('keeps the execution layer unaware of the paper simulation', () => {
    // Phase 9 measures the market; the execution layer presses a broker panel. Neither may
    // depend on the other, or a simulated outcome could end up gating a real press.
    for (const file of ['electron/main/execution-manager.ts', 'electron/main/order-executor.ts',
      'electron/main/order-panel.ts']) {
      const text = source(file)
      expect(text).not.toMatch(/PaperState|PaperTrade|paperResultLine|api\/paper/)
      expect(text).not.toMatch(/IPC_CHANNELS\.paper\b/)
    }
  })

  it('renders the paper panel as its own section, distinct from the order list', () => {
    const markup = renderToStaticMarkup(createElement(PaperPanel, { platform: 'capitalbear' }))
    expect(markup).toContain('ผลจำลอง (Paper)')
    // Before the first poll returns it says it has nothing, rather than showing a zero result.
    expect(markup).toContain('ยังไม่มีผลจำลอง')
    expect(markup).toContain('ยังไม่ได้ตั้งค่าเงินจำลอง')
    expect(markup).not.toContain('สำเร็จ (แผงตอบสนอง)')
    for (const control of ['Arm', 'หยุด', 'วัดตำแหน่งปุ่ม', 'AUTO'])
      expect(markup).not.toContain(control)
  })

  it('keeps the paper panel free of every broker control', () => {
    const text = source('src/renderer/src/components/PaperPanel.tsx')
    expect(text).not.toMatch(/execution\(|arm|disarm|calibrateControls|testControls/)
    expect(text).not.toMatch(/sendInputEvent|pressPoint|OrderExecutor|ExecutionManager/)
    // It reads one bridge method, and that method is a read.
    expect(text.match(/window\.quantScreenTrader\.\w+/g)).toEqual(['window.quantScreenTrader.paper'])
  })
})
