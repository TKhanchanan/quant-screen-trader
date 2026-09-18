import { describe, expect, it } from 'vitest'
import { armAvailable, armedLabel, defaultExecutionSettings, modeLocked, readyLine,
  type ExecutionCommand, type ExecutionMode, type ExecutionState } from '@quant-screen-trader/shared-types'
import { executionRefusal } from '../electron/main/execution-guard'

const state = (mode: ExecutionMode, armed: boolean): ExecutionState => ({ platform: 'capitalbear',
  settings: { ...defaultExecutionSettings(), mode }, armed, controls: null, controlsValid: false, blocked: [],
  lastBoardAsOf: null, ordersLastHour: 0, unverifiedInARow: 0, tickets: [], executionVersion: 'qst-execution-v1' })
const settings = (mode: ExecutionMode): ExecutionCommand =>
  ({ operation: 'settings', platform: 'capitalbear', settings: { ...defaultExecutionSettings(), mode } })

describe('execution guard at the IPC boundary', () => {
  it('never lets an armed PAPER executor become an armed AUTO one', () => {
    expect(executionRefusal(settings('AUTO'), state('PAPER', true), false)).toMatch(/^EXECUTION_ARMED/)
    expect(executionRefusal(settings('AUTO'), state('PAPER', false), false)).toBeNull()
    expect(executionRefusal(settings('AUTO'), state('AUTO', true), false)).toBeNull() // limits change, same mode
    expect(executionRefusal(settings('OFF'), state('PAPER', true), false)).toBeNull() // leaving to OFF disarms
  })
  it('keeps Phase 14 recording PAPER-only: no AUTO, no AUTO arm, no control test', () => {
    expect(executionRefusal(settings('AUTO'), state('OFF', false), true)).toMatch(/^SHADOW_LIVE_PAPER_ONLY/)
    expect(executionRefusal({ operation: 'arm', platform: 'capitalbear' }, state('AUTO', false), true)).toMatch(/^SHADOW_LIVE_PAPER_ONLY/)
    expect(executionRefusal({ operation: 'testControls', platform: 'capitalbear', confirm: 'PRESS ALL CONTROLS' },
      state('PAPER', true), true)).toMatch(/^SHADOW_LIVE_PAPER_ONLY/)
    expect(executionRefusal(settings('PAPER'), state('OFF', false), true)).toBeNull()
    expect(executionRefusal({ operation: 'arm', platform: 'capitalbear' }, state('PAPER', false), true)).toBeNull()
    expect(executionRefusal({ operation: 'calibrateControls', platform: 'capitalbear' }, state('PAPER', false), true)).toBeNull()
  })
  it('never refuses the stop control', () => {
    for (const shadow of [false, true])
      for (const mode of ['OFF', 'PAPER', 'AUTO'] as const)
        expect(executionRefusal({ operation: 'disarm', platform: 'capitalbear' }, state(mode, true), shadow)).toBeNull()
  })
})

describe('execution panel rules', () => {
  it('offers Arm in PAPER and AUTO, never in OFF or while armed', () => {
    expect(armAvailable(state('OFF', false))).toBe(false)
    expect(armAvailable(state('PAPER', false))).toBe(true)
    expect(armAvailable(state('AUTO', false))).toBe(true)
    expect(armAvailable(state('PAPER', true))).toBe(false)
  })
  it('locks the mode while armed and says whether a board will be pressed or only recorded', () => {
    expect(modeLocked(state('PAPER', true))).toBe(true)
    expect(modeLocked(state('PAPER', false))).toBe(false)
    expect(armedLabel(state('PAPER', true))).toContain('PAPER — ไม่กดจริง')
    expect(armedLabel(state('AUTO', true))).toContain('ARMED')
    expect(armedLabel(state('AUTO', false))).toBe('ยังไม่พร้อม')
    expect(readyLine(state('PAPER', true))).toContain('โดยไม่กดจริง')
    expect(readyLine(state('AUTO', true))).toContain('ถูกกดทันที')
    expect(readyLine(null)).toContain('กำลังอ่าน')
  })
})
