import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import {
  IPC_CHANNELS, DailySessionSchema, SessionGuardCommandSchema, SessionGuardStateSchema,
  ExecutionCommandSchema, ExecutionModeSchema, OrderDirectionSchema, ControlMapSchema,
  blockLabel, dailyPnlLabel, defaultSessionGuardSettings, nextResetLabel, progressPercent,
  sessionBlockLabel, sessionMoney, sessionStatusLabel,
  type SessionGuardState
} from '@quant-screen-trader/shared-types'
import { DailySessionPanel } from '../src/renderer/src/components/DailySessionPanel'

const session = {
  sessionId: '76f8c667-988d-50df-83c9-4eba5d27bda7', sessionDate: '2026-09-11',
  timezone: 'Asia/Bangkok', resetHour: 0, startedAt: 1_789_102_800_000, completedAt: null,
  nextResetAt: 1_789_146_000_000, status: 'ACTIVE', accountingSource: 'PAPER', currency: 'THB',
  profitTarget: 600, lossLimit: 300, realizedPnl: 427, grossProfit: 500, grossLoss: 73,
  wins: 9, losses: 5, draws: 1, invalid: 0, resolvedTrades: 15, monetaryTrades: 15,
  openTrades: 1, largestWin: 82, largestLoss: -50, peakRealizedPnl: 470,
  troughRealizedPnl: 0, maxRealizedDrawdown: 43, targetReachedAt: null, lossLimitReachedAt: null,
  tradesToTarget: null, stopReason: null, canOpenNewEntry: true, blockReason: null,
  currencyMismatches: 0, nonMonetarySettlements: 0, sessionGuardVersion: 'qst-session-guard-v1'
}
const state = (changes: Record<string, unknown> = {}, sessionChanges: Record<string, unknown> = {}):
  SessionGuardState => SessionGuardStateSchema.parse({
    sessionGuardVersion: 'qst-session-guard-v1', available: true, enabled: true,
    canOpenNewEntry: true, blockReason: null, shutdownRequested: false,
    paperAccountingConfigured: true, settingsError: null,
    session: { ...session, ...sessionChanges }, settings: defaultSessionGuardSettings(),
    targetProgress: 427 / 600, lossProgress: 0, remainingToTarget: 173, nextResetAt: 1_789_146_000_000,
    openTrades: 1, notifications: [], history: [], ...changes
  })
const source = (file: string): string => readFileSync(join(import.meta.dirname, '..', file), 'utf8')

describe('daily session bridge', () => {
  it('accepts an engine session and tolerates fields the desktop does not render', () => {
    const parsed = state({}, { peakRealizedPnl: 470, extraDiagnostic: 7 })
    expect(parsed.session?.realizedPnl).toBe(427)
    expect(IPC_CHANNELS.sessionGuard).toBe('session-guard:command')
  })

  it('rejects an unreadable session rather than inventing a total', () => {
    expect(DailySessionSchema.safeParse({ ...session, realizedPnl: 'lots' }).success).toBe(false)
    expect(DailySessionSchema.safeParse({ ...session, status: 'WINNING' }).success).toBe(false)
    expect(DailySessionSchema.safeParse({ ...session, profitTarget: 0 }).success).toBe(false)
    expect(DailySessionSchema.safeParse({ ...session, lossLimit: -300 }).success).toBe(false)
    expect(DailySessionSchema.safeParse({ ...session, largestLoss: 50 }).success).toBe(false)
    expect(DailySessionSchema.safeParse({ ...session, accountingSource: 'BROKER' }).success).toBe(false)
  })

  it('refuses a command that would set a limit of zero or an unknown shape', () => {
    const settings = defaultSessionGuardSettings()
    expect(SessionGuardCommandSchema.safeParse({ operation: 'stop' }).success).toBe(true)
    expect(SessionGuardCommandSchema.safeParse({ operation: 'state' }).success).toBe(true)
    expect(SessionGuardCommandSchema.safeParse({
      operation: 'settings', settings: { ...settings, dailyProfitTarget: 0 } }).success).toBe(false)
    expect(SessionGuardCommandSchema.safeParse({
      operation: 'settings', settings: { ...settings, resetHour: 24 } }).success).toBe(false)
    expect(SessionGuardCommandSchema.safeParse({ operation: 'resume' }).success).toBe(false)
  })

  it('reports an unreachable engine without inventing a permission either way', () => {
    const offline = SessionGuardStateSchema.parse({
      sessionGuardVersion: 'unknown', available: false, enabled: false, canOpenNewEntry: true,
      blockReason: null, shutdownRequested: false, paperAccountingConfigured: false,
      settingsError: null, session: null, settings: defaultSessionGuardSettings(),
      targetProgress: null, lossProgress: null, remainingToTarget: null, nextResetAt: null,
      openTrades: 0, notifications: [], history: []
    })
    expect(sessionStatusLabel(offline.session?.status ?? null)).toBe('ยังไม่เริ่มรอบวันนี้')
    expect(dailyPnlLabel(offline)).toBe('ยังไม่มีรอบวันนี้')
    expect(nextResetLabel(offline)).toBe('—')
  })

  it('never renders an unpriced day as zero profit', () => {
    // Phase 9 reports money only when a simulated stake and payout were configured. ฿0 would
    // read as break-even on a day nobody priced.
    const unpriced = state({ paperAccountingConfigured: false }, { monetaryTrades: 0, realizedPnl: 0 })
    expect(dailyPnlLabel(unpriced)).toBe('ยังไม่ได้ตั้งค่าเงินจำลอง')
    expect(dailyPnlLabel(state())).toBe('+฿427.00')
    expect(sessionMoney(-315, 'THB')).toBe('−฿315.00')
    expect(sessionMoney(0, 'THB')).toBe('฿0.00')
    expect(sessionMoney(41, 'USD')).toBe('+USD 41.00')
  })

  it('shows profit progress and loss progress as separate bounded numbers', () => {
    expect(progressPercent(427 / 600)).toBe('71%')
    expect(progressPercent(null)).toBe('—')
    // An overshoot is full, not more than full, and a losing day is zero toward a profit target.
    expect(progressPercent(1)).toBe('100%')
    expect(progressPercent(0)).toBe('0%')
  })

  it('says why new entries are refused, and that a disabled guard refuses nothing', () => {
    expect(sessionBlockLabel(state())).toBe('เข้าไม้ใหม่ได้')
    expect(sessionBlockLabel(state({ canOpenNewEntry: false, blockReason: 'DAILY_PROFIT_TARGET' })))
      .toBe('ถึงเป้ากำไรของวันแล้ว')
    expect(sessionBlockLabel(state({ canOpenNewEntry: false, blockReason: 'DAILY_LOSS_LIMIT' })))
      .toBe('ถึงขีดขาดทุนของวันแล้ว')
    expect(sessionBlockLabel(state({ canOpenNewEntry: false, blockReason: 'LOCKED_FOR_DAY' })))
      .toBe('ล็อกไว้จนถึงรอบถัดไป')
    // GUARD_DISABLED is never a refusal; it explains that nothing is being enforced.
    const off = state({ enabled: false, blockReason: 'GUARD_DISABLED' })
    expect(off.canOpenNewEntry).toBe(true)
    expect(sessionBlockLabel(off)).toBe('ไม่ได้คุมรอบ — ไม่ได้ห้ามอะไร')
  })

  it('labels every stopped status without claiming anything about tomorrow', () => {
    const rendered = [
      sessionStatusLabel('TARGET_REACHED'), sessionStatusLabel('LOSS_LIMIT_REACHED'),
      sessionStatusLabel('LOCKED_FOR_DAY'), sessionStatusLabel('ACCOUNTING_ERROR'),
      sessionStatusLabel('WAITING_FOR_SETTLEMENT'), sessionBlockLabel(state())
    ].join(' ')
    for (const claim of ['แม่น', 'การันตี', 'กำไรแน่', 'ทบเงิน', 'แก้ไม้'])
      expect(rendered).not.toContain(claim)
  })

  it('renders the panel with the day, the limits and a stop that is not disarm', () => {
    const markup = renderToStaticMarkup(createElement(DailySessionPanel, { onError: () => {} }))
    expect(markup).toContain('รอบวัน (Daily Session)')
    expect(markup).toContain('หยุดรอบวันนี้')
    expect(markup).toContain('เป้ากำไร')
    expect(markup).toContain('ขีดขาดทุน')
    // Stopping the day and disarming the executor are different controls with different words.
    expect(markup).not.toContain('Arm')
    expect(markup).not.toContain('วัดตำแหน่งปุ่ม')
    expect(markup).not.toContain('AUTO')
  })

  it('exposes reading the day and no way to open a position', () => {
    expect(Object.keys(IPC_CHANNELS)).toContain('sessionGuard')
    for (const channel of Object.values(IPC_CHANNELS))
      expect(channel).not.toMatch(/order|trade|stake|execute/i)
    expect(Object.keys(state().session!)).not.toContain('stake')
    expect(Object.keys(state().session!)).not.toContain('rankScore')
  })
})

describe('execution is gated, never replaced', () => {
  it('keeps every execution capability the layer already had', () => {
    // T-DB. Phase 9.5 adds a veto. It removes nothing: AUTO, PAPER, Arm, Disarm, the control
    // map, the Higher/Lower mapping and the all-controls test all still exist.
    expect(ExecutionModeSchema.options).toEqual(['OFF', 'PAPER', 'AUTO'])
    expect(OrderDirectionSchema.options).toEqual(['HIGHER', 'LOWER'])
    expect(ControlMapSchema.safeParse({
      platform: 'capitalbear', surfaceRevision: 1, zoomFactor: 1, directionForGreen: 'HIGHER',
      slots: [], reasons: [], measuredAt: '2026-09-11T04:00:00.000Z'
    }).success).toBe(true)
    for (const operation of ['state', 'arm', 'disarm', 'calibrateControls', 'directionForGreen'])
      expect(ExecutionCommandSchema.safeParse(
        { operation, platform: 'capitalbear', direction: 'HIGHER' }).success).toBe(true)
    expect(ExecutionCommandSchema.safeParse({
      operation: 'testControls', platform: 'capitalbear', confirm: 'PRESS ALL CONTROLS'
    }).success).toBe(true)

    const manager = source('electron/main/execution-manager.ts')
    for (const capability of ["case 'arm'", "case 'disarm'", "case 'calibrateControls'",
      "case 'testControls'", "case 'directionForGreen'", "'PAPER'", 'maxOrdersPerHour',
      'cooldownMs', 'UNVERIFIED_BREAKER', 'TAB_IDENTITY_UNCERTAIN'])
      expect(manager).toContain(capability)
    expect(source('electron/main/order-executor.ts')).toContain('pressPoint')
    expect(source('electron/main/order-panel.ts').length).toBeGreaterThan(0)
  })

  it('adds the daily veto as one more refusal and nothing else', () => {
    const manager = source('electron/main/execution-manager.ts')
    expect(manager).toContain('/api/session-guard/state')
    expect(manager).toContain("reasons.push(this.sessionBlock)")
    // The guard can only ever withdraw permission. Nothing in the execution layer may read a
    // daily total and change a stake, a score or a gate because of it.
    for (const forbidden of ['dailyProfitTarget', 'realizedPnl', 'stakeMultiplier', 'martingale',
      'minRankScore =', 'increaseStake'])
      expect(manager).not.toContain(forbidden)
    expect(blockLabel('SESSION_DAILY_LOSS_LIMIT')).toContain('ขีดขาดทุน')
    expect(blockLabel('SESSION_LOCKED_FOR_DAY')).toContain('ล็อก')
  })

  it('keeps the session panel free of every broker control', () => {
    const panel = source('src/renderer/src/components/DailySessionPanel.tsx')
    expect(panel).not.toMatch(/sendInputEvent|pressPoint|OrderExecutor|calibrateControls/)
    expect(panel.match(/window\.quantScreenTrader\.\w+/g))
      .toEqual(['window.quantScreenTrader.sessionGuard', 'window.quantScreenTrader.sessionGuard',
        'window.quantScreenTrader.sessionGuard'])
  })

  it('lets Python report a close and never take one', () => {
    const watcher = source('electron/main/session-watcher.ts')
    expect(watcher).toContain('shutdownRequested')
    expect(watcher).toContain('this.quit()')
    // Electron owns the lifetime. Nothing forces the process down.
    expect(watcher).not.toMatch(/process\.exit|app\.exit|SIGKILL|destroy\(\)/)
  })
})
