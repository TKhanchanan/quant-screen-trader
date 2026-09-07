import type { EngineHealthSnapshot } from '@quant-screen-trader/shared-types'
import type { JSX } from 'react'

interface EngineStatusProps {
  health: EngineHealthSnapshot
}

export function EngineStatus({ health }: EngineStatusProps): JSX.Element {
  return (
    <span className={`status status--${health.state}`} role="status">
      <span className="status__dot" aria-hidden="true" />
      {health.state.toUpperCase()}
    </span>
  )
}
