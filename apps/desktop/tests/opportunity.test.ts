import { describe, expect, it } from 'vitest'
import {
  IPC_CHANNELS, OpportunityBoardSchema, OpportunityResponseSchema, OpportunityStateSchema,
  WatchlistEntrySchema, boardLabel, candidateLabel, leadLabel, type OpportunityBoard
} from '@quant-screen-trader/shared-types'

const versions = {
  featureVersion: 'qfe-v2', regimeVersion: 'qst-regime-v1',
  strategyVersion: 'qst-strategy-v1', rankingVersion: 'qst-ranking-v1'
}
const candidate = {
  platform: 'capitalbear', slotId: 4, assetName: 'EUR/USD OTC', asOf: 1_788_873_600_000,
  direction: 'UP', primaryRegime: 'TREND_UP', ensembleConfidence: 0.78, rankScore: 0.74,
  candidateStatus: 'ACTIONABLE', rank: 1, rankReasonCodes: ['TOP_CANDIDATE'], exclusionReasons: []
}
// Parsed rather than asserted: the fixture the labels are proved against has to be a value
// the contract actually accepts, not one a cast made look like one.
const entry = WatchlistEntrySchema.parse({
  rank: 1, slotId: 4, assetName: 'EUR/USD OTC', direction: 'UP', rankScore: 0.74,
  ensembleConfidence: 0.78, regime: 'TREND_UP', candidateStatus: 'ACTIONABLE'
})
const board = {
  ...versions, platform: 'capitalbear', asOf: 1_788_873_600_000, primaryTimeframe: 'S5',
  status: 'READY', expectedSlots: 5, receivedSlots: 5, rankedSlots: 3, excludedSlots: 1,
  missingSlots: [], candidates: [candidate], selectedSlotId: 4, selectedAssetName: 'EUR/USD OTC',
  selectedDirection: 'UP', selectedScore: 0.74, runnerUpSlotId: 7, leadMargin: 0.11,
  watchlist: [entry], reasons: ['COHORT_COMPLETE']
}

const parsed = (changes: Record<string, unknown> = {}): OpportunityBoard =>
  OpportunityBoardSchema.parse({ ...board, ...changes })

describe('opportunity board bridge', () => {
  it('accepts an engine board and tolerates fields the desktop does not render', () => {
    const state = OpportunityStateSchema.parse({
      rankingVersion: 'qst-ranking-v1', available: true, board
    })
    expect(state.board?.selectedSlotId).toBe(4)
    expect(state.board?.candidates[0]!.rankScore).toBe(0.74)
    expect(IPC_CHANNELS.opportunities).toBe('opportunities:state')
    // The engine also returns its gate constants and the expected slot list; parsing that
    // payload with the strict desktop contract would make every poll look like an outage.
    const payload = { ...versions, board, minSelectionScore: 0.35, expectedSlots: [1, 2, 4] }
    expect(OpportunityResponseSchema.parse(payload).board.status).toBe('READY')
    expect(OpportunityStateSchema.safeParse(payload).success).toBe(false)
  })

  it('rejects an unreadable board rather than inventing a ranking', () => {
    expect(OpportunityBoardSchema.safeParse({ ...board, status: 'ENTER' }).success).toBe(false)
    expect(OpportunityBoardSchema.safeParse({ ...board, selectedScore: 1.4 }).success).toBe(false)
    expect(OpportunityBoardSchema.safeParse({ ...board, selectedDirection: 'BUY' }).success).toBe(false)
    expect(OpportunityBoardSchema.safeParse({ ...board, expectedSlots: 12 }).success).toBe(false)
    expect(OpportunityBoardSchema.safeParse({
      ...board, candidates: [{ ...candidate, candidateStatus: 'TRADE' }]
    }).success).toBe(false)
  })

  it('reports an unreachable engine without fabricating a board', () => {
    const offline = OpportunityStateSchema.parse({
      rankingVersion: 'unknown', available: false, board: null
    })
    expect(offline.board).toBeNull()
    expect(boardLabel(offline.board)).toBe('ยังไม่มีบอร์ด')
    expect(leadLabel(offline.board)).toBe('—')
  })

  it('labels an incomplete cohort as collecting rather than as a ranking', () => {
    const collecting = parsed({
      status: 'COLLECTING', receivedSlots: 3, missingSlots: [7, 9],
      selectedSlotId: null, selectedAssetName: null, selectedDirection: null,
      selectedScore: null, runnerUpSlotId: null, leadMargin: null,
      reasons: ['COHORT_INCOMPLETE', 'MISSING_SLOTS']
    })
    expect(boardLabel(collecting)).toBe('กำลังเก็บข้อมูล')
    expect(leadLabel(collecting)).toBe('ข้อมูลรอบนี้ไม่ครบ')
  })

  it('says why no leader was named instead of showing false certainty', () => {
    expect(leadLabel(parsed({
      status: 'NO_OPPORTUNITY', selectedSlotId: null, selectedScore: null,
      selectedAssetName: null, selectedDirection: null, leadMargin: 0.01,
      reasons: ['LOW_LEAD_MARGIN', 'COHORT_COMPLETE']
    }))).toBe('ไม่มีตัวนำชัดเจน (ทิ้งห่างน้อยไป)')
    expect(leadLabel(parsed({
      status: 'NO_OPPORTUNITY', selectedSlotId: null, selectedScore: null,
      selectedAssetName: null, selectedDirection: null, runnerUpSlotId: null, leadMargin: null,
      reasons: ['BELOW_SELECTION_SCORE', 'COHORT_COMPLETE']
    }))).toBe('คะแนนสูงสุดยังต่ำกว่าเกณฑ์คัดเลือก')
    expect(leadLabel(parsed({ leadMargin: null, runnerUpSlotId: null })))
      .toBe('นำอยู่ — (ตัวเดียวในรอบ)')
    expect(leadLabel(parsed())).toBe('นำอยู่ +0.11')
    expect(boardLabel(parsed({ status: 'PARTIAL' }))).toBe('ข้อมูลไม่ครบ (PARTIAL)')
    expect(boardLabel(parsed({ status: 'INVALID' }))).toBe('ข้อมูลใช้ไม่ได้')
  })

  it('never renders a ranking as an instruction to trade', () => {
    const rendered = [
      candidateLabel(entry), boardLabel(parsed()), leadLabel(parsed()),
      boardLabel(parsed({ status: 'NO_OPPORTUNITY' }))
    ].join(' ').toLowerCase()
    for (const verb of ['buy', 'sell', 'enter', 'bet', 'place trade', 'stake', 'payout', 'win rate'])
      expect(rendered).not.toContain(verb)
    // The labels are Thai now, so the same guarantee is asserted in Thai: a direction may be
    // named, an action may not. "ขึ้น"/"ลง" describe where the analysis points; these do not.
    for (const verb of ['ซื้อ', 'ขาย', 'เข้าไม้', 'เดิมพัน', 'ลงเงิน', 'ชนะ', 'ควรเข้า'])
      expect(rendered).not.toContain(verb)
    expect(candidateLabel(entry)).toContain('#1 ช่อง 4 · EUR/USD OTC · ขึ้น')
    expect(candidateLabel(entry)).toContain('คะแนน 0.74')
  })

  it('keeps the two platform boards separate rather than merging them into one list', () => {
    const capitalbear = parsed()
    const iqoption = parsed({ platform: 'iqoption', primaryTimeframe: 'M1', selectedSlotId: 1 })
    expect(capitalbear.primaryTimeframe).toBe('S5')
    expect(iqoption.primaryTimeframe).toBe('M1')
    // Rank one exists on each board. There is no combined 1-18 ordering to render.
    expect(capitalbear.watchlist[0]!.rank).toBe(1)
    expect(iqoption.watchlist[0]!.rank).toBe(1)
    expect(Object.keys(capitalbear)).not.toContain('globalRank')
  })

  it('exposes reading a board and no way to act on one', () => {
    expect(Object.keys(IPC_CHANNELS)).toContain('opportunities')
    for (const channel of Object.values(IPC_CHANNELS))
      expect(channel).not.toMatch(/order|trade|stake|execute/i)
    expect(Object.keys(parsed())).not.toContain('selectedStake')
    expect(Object.keys(parsed().candidates[0]!)).not.toContain('positionSize')
  })
})
