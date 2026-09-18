import type { EngineHealthSnapshot } from '@quant-screen-trader/shared-types'
import type { JSX } from 'react'

interface EngineStatusProps {
  health: EngineHealthSnapshot
}

export function EngineStatus({ health }: EngineStatusProps): JSX.Element {
  return (
    <span className={`status status--${health.state}`} role="status">
      <span className="status__dot" aria-hidden="true" />
      {{ online: 'เชื่อมต่อแล้ว', offline: 'ยังไม่เชื่อมต่อ', degraded: 'การเชื่อมต่อไม่สมบูรณ์' }[health.state]}
    </span>
  )
}
