import type { ExecutionCommand, ExecutionState } from '@quant-screen-trader/shared-types'

/**
 * Refusals applied at the IPC boundary, in front of the accepted ExecutionManager (which is not
 * modified). Each one can only withhold an operation; none can arm, press or change a limit.
 *
 * - Always: an armed executor cannot be switched into AUTO. The manager keeps `armed` across a
 *   mode change to AUTO, so PAPER-then-AUTO would arm real orders without an Arm press.
 * - Phase 14 recording (QST_SHADOW_LIVE=1): PAPER only. AUTO cannot be selected or armed and the
 *   all-controls test cannot run, because the soak must place no order and press no broker control.
 * Disarm is never refused.
 */
export function executionRefusal(command: ExecutionCommand, current: ExecutionState, shadowLive: boolean): string | null {
  if (command.operation === 'disarm') return null
  if (command.operation === 'settings' && command.settings.mode === 'AUTO' &&
    current.armed && current.settings.mode !== 'AUTO')
    return 'EXECUTION_ARMED: กดหยุด (Disarm) ก่อนเปลี่ยนเป็นโหมด AUTO'
  if (!shadowLive) return null
  if (command.operation === 'settings' && command.settings.mode === 'AUTO')
    return 'SHADOW_LIVE_PAPER_ONLY: ระหว่างบันทึก Phase 14 ใช้ได้แค่ OFF หรือ PAPER'
  if (command.operation === 'arm' && current.settings.mode === 'AUTO')
    return 'SHADOW_LIVE_PAPER_ONLY: ระหว่างบันทึก Phase 14 Arm ได้เฉพาะโหมด PAPER'
  if (command.operation === 'testControls')
    return 'SHADOW_LIVE_PAPER_ONLY: ห้ามทดสอบกดปุ่มจริงระหว่างบันทึก Phase 14'
  return null
}
