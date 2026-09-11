import { randomUUID } from 'node:crypto'
import { calibrationZoomMatches, defaultExecutionSettings, directionForVote, EXECUTION_VERSION,
  ExecutionStateSchema, OpportunityResponseSchema, OrderTicketSchema,
  type ConfigurationResult, type ControlMap, type ExecutionCommand, type ExecutionSettings,
  type ExecutionState, type OpportunityBoard, type OrderTicket, type Platform } from '@quant-screen-trader/shared-types'
import type { EngineConnectionConfig } from './engine-config'
import type { OrderExecutor } from './order-executor'
import type { PlatformBrowserManager } from './platform-browser'

/**
 * Turns a finished opportunity board into at most one press.
 *
 * The engine's selection gate runs first and is not re-implemented here; this layer adds the
 * operator's own limits on top of it, and every refusal is a named code rather than a silent
 * skip, so an armed executor that never fires can always say why.
 *
 * One board produces at most one ticket. Boards are identified by their close time, so a slow
 * tick, a duplicated poll or a restarted renderer cannot press the same decision twice.
 */

const TICKET_HISTORY = 50
const HOUR_MS = 3_600_000

interface WorkspaceExecution {
  settings: ExecutionSettings
  armed: boolean
  controls: ControlMap | null
  config: ConfigurationResult | null
  lastBoardAsOf: number | null
  lastPressAt: number | null
  pressTimestamps: number[]
  unverifiedInARow: number
  tickets: OrderTicket[]
  busy: boolean
  /** An operator control test in flight. Holds off the board loop and a second test. */
  testing: boolean
}

function blankWorkspace(): WorkspaceExecution {
  return { settings: defaultExecutionSettings(), armed: false, controls: null, config: null,
    lastBoardAsOf: null, lastPressAt: null, pressTimestamps: [], unverifiedInARow: 0, tickets: [],
    busy: false, testing: false }
}

export class ExecutionManager {
  private readonly workspaces = new Map<Platform, WorkspaceExecution>()
  private readonly timer: ReturnType<typeof setInterval>
  constructor(private readonly browsers: PlatformBrowserManager, private readonly executor: OrderExecutor,
    private readonly connection: EngineConnectionConfig) {
    this.timer = setInterval(() => { void this.tick() }, 1000)
  }

  private workspace(platform: Platform): WorkspaceExecution {
    const existing = this.workspaces.get(platform)
    if (existing) return existing
    const created = blankWorkspace()
    this.workspaces.set(platform, created)
    return created
  }

  /**
   * Configuration carries the calibration the controls were measured against. A new profile
   * invalidates the map outright: the same coordinates on a re-laid grid are a click on a chart.
   */
  configure(config: ConfigurationResult): void {
    const platform = config.configuration.platform, workspace = this.workspace(platform)
    const profileOf = (value: ConfigurationResult | null): string =>
      JSON.stringify(value?.calibrations.find(p => p.id === value.activeCalibrationId) ?? null)
    if (profileOf(workspace.config) !== profileOf(config)) workspace.controls = null
    workspace.config = config
  }

  async command(command: ExecutionCommand): Promise<ExecutionState> {
    const workspace = this.workspace(command.platform)
    switch (command.operation) {
      case 'state': break
      case 'settings':
        workspace.settings = command.settings
        // Leaving AUTO cannot leave an armed executor behind.
        if (command.settings.mode !== 'AUTO') workspace.armed = false
        break
      case 'arm': {
        const blocked = this.blockedReasons(command.platform, workspace).filter(code => code !== 'NOT_ARMED')
        if (blocked.length) throw new Error(`EXECUTION_BLOCKED: ${blocked.join(', ')}`)
        workspace.unverifiedInARow = 0
        workspace.armed = true
        break
      }
      // The stop control. No gate, breaker or missing state can refuse it.
      case 'disarm': workspace.armed = false; workspace.testing = false; break
      case 'calibrateControls': {
        const profile = workspace.config?.calibrations.find(p => p.id === workspace.config!.activeCalibrationId)
        if (!profile) throw new Error('CONTROLS_UNCALIBRATED: select a calibration profile first.')
        workspace.controls = await this.executor.calibrate(command.platform, profile,
          workspace.controls?.directionForGreen ?? 'HIGHER')
        break
      }
      case 'testControls': {
        if (workspace.testing) throw new Error('CONTROL_TEST_RUNNING: a control test is already in flight.')
        if (!workspace.controls?.slots.length) throw new Error('CONTROLS_UNCALIBRATED: measure the order controls first.')
        if (!this.controlsValid(command.platform, workspace))
          throw new Error('CONTROL_MAP_STALE: measure the order controls again before testing them.')
        const surface = this.browsers.observationSurface(command.platform)
        if (!surface.available || surface.paused || !surface.gridReady)
          throw new Error('PRESS_UNAVAILABLE: the platform browser is not showing a verified grid.')
        workspace.testing = true
        // Fire and forget: eighteen presses take far longer than an IPC round trip, and the
        // operator watches the tickets arrive rather than waiting on one blocked call.
        void this.runControlTest(command.platform, workspace).finally(() => { workspace.testing = false })
        break
      }
      case 'directionForGreen': {
        if (!workspace.controls) throw new Error('CONTROLS_UNCALIBRATED: measure the order controls first.')
        if (workspace.controls.directionForGreen !== command.direction)
          workspace.controls = { ...workspace.controls, directionForGreen: command.direction,
            slots: workspace.controls.slots.map(slot => ({ ...slot, higher: slot.lower, lower: slot.higher })) }
        break
      }
    }
    return this.state(command.platform)
  }

  state(platform: Platform): ExecutionState {
    const workspace = this.workspace(platform)
    return ExecutionStateSchema.parse({ platform, settings: workspace.settings, armed: workspace.armed,
      controls: workspace.controls, controlsValid: this.controlsValid(platform, workspace),
      blocked: this.blockedReasons(platform, workspace), lastBoardAsOf: workspace.lastBoardAsOf,
      ordersLastHour: this.recentPresses(workspace).length, unverifiedInARow: workspace.unverifiedInARow,
      tickets: workspace.tickets, executionVersion: EXECUTION_VERSION })
  }

  private recentPresses(workspace: WorkspaceExecution): number[] {
    const since = Date.now() - HOUR_MS
    workspace.pressTimestamps = workspace.pressTimestamps.filter(at => at >= since)
    return workspace.pressTimestamps
  }

  /** A map is only usable on the surface it was measured on, at the zoom it was measured at. */
  private controlsValid(platform: Platform, workspace: WorkspaceExecution): boolean {
    const controls = workspace.controls
    if (!controls || !controls.slots.length) return false
    const surface = this.browsers.observationSurface(platform)
    const profile = workspace.config?.calibrations.find(p => p.id === workspace.config!.activeCalibrationId)
    return surface.revision === controls.surfaceRevision &&
      Math.abs(surface.zoomFactor - controls.zoomFactor) <= .001 &&
      !!profile && calibrationZoomMatches(profile, surface)
  }

  /** Everything standing between an armed executor and its next press, named. */
  private blockedReasons(platform: Platform, workspace: WorkspaceExecution): string[] {
    const reasons: string[] = []
    if (workspace.settings.mode === 'OFF') reasons.push('MODE_OFF')
    if (!workspace.armed) reasons.push('NOT_ARMED')
    const surface = this.browsers.observationSurface(platform)
    if (!surface.available || surface.paused || !surface.gridReady) reasons.push('SURFACE_UNAVAILABLE')
    if (!workspace.controls) reasons.push('NO_CONTROL_MAP')
    else if (!this.controlsValid(platform, workspace)) reasons.push('CONTROL_MAP_STALE')
    const cap = workspace.settings.limits.maxOrdersPerHour
    if (this.recentPresses(workspace).length >= cap) reasons.push('HOURLY_CAP')
    if (workspace.lastPressAt !== null && Date.now() - workspace.lastPressAt < workspace.settings.limits.cooldownMs)
      reasons.push('COOLDOWN')
    if (workspace.unverifiedInARow >= workspace.settings.limits.maxUnverifiedInARow) reasons.push('UNVERIFIED_BREAKER')
    return reasons
  }

  private async board(platform: Platform): Promise<OpportunityBoard | null> {
    try {
      const response = await fetch(new URL(`/api/opportunities/${platform}`, this.connection.healthUrl),
        { signal: AbortSignal.timeout(2000), redirect: 'error' })
      if (!response.ok) return null
      return OpportunityResponseSchema.parse(await response.json()).board
    } catch { return null }
  }

  private async tick(): Promise<void> {
    for (const [platform, workspace] of this.workspaces) {
      if (workspace.busy || workspace.testing || !workspace.armed || workspace.settings.mode === 'OFF') continue
      if (this.blockedReasons(platform, workspace).length) continue
      workspace.busy = true
      try { await this.evaluate(platform, workspace) }
      catch { /* One failed cycle never disarms; the next board is evaluated on its own merits. */ }
      finally { workspace.busy = false }
    }
  }

  private async evaluate(platform: Platform, workspace: WorkspaceExecution): Promise<void> {
    const board = await this.board(platform)
    if (!board) return
    // One board, one decision. Acting on a close already seen would double an entry whenever a
    // poll overlaps a slow press.
    if (workspace.lastBoardAsOf !== null && board.asOf <= workspace.lastBoardAsOf) return
    workspace.lastBoardAsOf = board.asOf

    const limits = workspace.settings.limits
    const reasons: string[] = []
    if (!limits.acceptBoardStatus.includes(board.status)) reasons.push('BOARD_STATUS')
    if (board.selectedSlotId === null) reasons.push('NO_SELECTION')
    const direction = board.selectedDirection ? directionForVote(board.selectedDirection) : null
    if (!direction) reasons.push('NO_SELECTION')
    const candidate = board.candidates.find(entry => entry.slotId === board.selectedSlotId)
    if ((board.selectedScore ?? 0) < limits.minRankScore ||
      (candidate?.ensembleConfidence ?? 0) < limits.minEnsembleConfidence) reasons.push('BELOW_LIMITS')
    if (board.selectedSlotId !== null && !workspace.settings.allowedSlots.includes(board.selectedSlotId))
      reasons.push('SLOT_NOT_ALLOWED')
    if (reasons.length || board.selectedSlotId === null || !direction) return

    const slotId = board.selectedSlotId
    const assetName = board.selectedAssetName ?? candidate?.assetName ?? ''
    const ticket: OrderTicket = OrderTicketSchema.parse({ id: randomUUID(), platform, slotId, assetName, direction,
      boardAsOf: board.asOf, rankScore: board.selectedScore ?? 0,
      ensembleConfidence: candidate?.ensembleConfidence ?? 0, state: 'BLOCKED', reasons: [],
      requestedAt: new Date().toISOString(), pressedAt: null, latencyMs: null })

    // The tab identity is re-proved here, immediately before the press, exactly as the capture
    // pipeline does: a control map addresses a canvas cell, and which asset that cell shows is
    // only known while the broker's own tab order has not moved.
    let canvasSlotId: number
    try { canvasSlotId = this.browsers.chartSlot(platform, slotId, assetName) }
    catch (error) {
      this.record(workspace, { ...ticket, state: 'BLOCKED',
        reasons: [error instanceof Error && error.message.startsWith('TAB') ? 'TAB_IDENTITY_UNCERTAIN' : 'SLOT_UNRESOLVED'] })
      return
    }
    const controls = workspace.controls!.slots.find(slot => slot.slotId === canvasSlotId)
    if (!controls) { this.record(workspace, { ...ticket, state: 'BLOCKED', reasons: ['CELL_NOT_MEASURED'] }); return }
    if (controls.confidence < limits.minControlConfidence) {
      this.record(workspace, { ...ticket, state: 'BLOCKED', reasons: ['CONTROL_CONFIDENCE_LOW'] }); return
    }
    if (workspace.settings.mode === 'PAPER') {
      this.record(workspace, { ...ticket, state: 'PAPER', reasons: ['NOT_SENT'] }); return
    }

    try {
      const result = await this.executor.press(platform, workspace.controls!, canvasSlotId, direction)
      workspace.lastPressAt = result.pressedAt
      workspace.pressTimestamps.push(result.pressedAt)
      workspace.unverifiedInARow = result.verified ? 0 : workspace.unverifiedInARow + 1
      // Presses the broker never visibly answered are the one failure this layer cannot see past,
      // so a run of them takes the arm away rather than continuing to send into the dark.
      if (workspace.unverifiedInARow >= limits.maxUnverifiedInARow) workspace.armed = false
      this.record(workspace, { ...ticket, state: result.verified ? 'CONFIRMED' : 'UNVERIFIED',
        reasons: result.reasons, pressedAt: new Date(result.pressedAt).toISOString(), latencyMs: result.latencyMs })
    } catch (error) {
      this.record(workspace, { ...ticket, state: 'FAILED',
        reasons: [error instanceof Error ? error.message.split(':')[0]! : 'PRESS_FAILED'] })
    }
  }

  /**
   * Press every measured control once, both directions, cell by cell.
   *
   * This is a coordinate proof, not a strategy: it ignores the board, the score limits and the
   * rate limits, because the operator asked for exactly these presses. It still records every
   * one as a ticket and still counts them against the hourly total, so an executor armed
   * afterwards is throttled by what the test already sent.
   */
  private async runControlTest(platform: Platform, workspace: WorkspaceExecution): Promise<void> {
    const map = workspace.controls!
    for (const controls of [...map.slots].sort((a, b) => a.slotId - b.slotId)) {
      for (const direction of ['HIGHER', 'LOWER'] as const) {
        if (!workspace.testing) return
        const ticket: OrderTicket = OrderTicketSchema.parse({ id: randomUUID(), platform,
          slotId: controls.slotId, assetName: `cell ${controls.slotId}`, direction, boardAsOf: 0,
          rankScore: 0, ensembleConfidence: 0, state: 'BLOCKED', reasons: ['CONTROL_TEST'],
          requestedAt: new Date().toISOString(), pressedAt: null, latencyMs: null })
        try {
          const result = await this.executor.press(platform, map, controls.slotId, direction)
          workspace.lastPressAt = result.pressedAt
          workspace.pressTimestamps.push(result.pressedAt)
          this.record(workspace, { ...ticket, state: result.verified ? 'CONFIRMED' : 'UNVERIFIED',
            reasons: ['CONTROL_TEST', ...result.reasons].slice(0, 12),
            pressedAt: new Date(result.pressedAt).toISOString(), latencyMs: result.latencyMs })
        } catch (error) {
          this.record(workspace, { ...ticket, state: 'FAILED',
            reasons: ['CONTROL_TEST', error instanceof Error ? error.message.split(':')[0]! : 'PRESS_FAILED'] })
          // A refused surface will refuse every remaining press too; stop rather than log eighteen.
          if (error instanceof Error && error.message.startsWith('PRESS_')) return
        }
        await new Promise(resolve => setTimeout(resolve, 700))
      }
    }
  }

  private record(workspace: WorkspaceExecution, ticket: OrderTicket): void {
    workspace.tickets = [OrderTicketSchema.parse(ticket), ...workspace.tickets].slice(0, TICKET_HISTORY)
  }

  stop(): void {
    clearInterval(this.timer)
    for (const workspace of this.workspaces.values()) workspace.armed = false
  }
}
