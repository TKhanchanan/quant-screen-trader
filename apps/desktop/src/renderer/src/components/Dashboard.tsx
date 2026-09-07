import { useState, type JSX } from 'react'
import type { Platform } from '@quant-screen-trader/shared-types'
import { PLATFORMS, PLATFORM_DETAILS } from '../platforms'
import { useAppStore } from '../state/appStore'
import { EngineStatus } from './EngineStatus'

export function Dashboard(): JSX.Element {
  const engineHealth = useAppStore((state) => state.engineHealth)
  const [actionError, setActionError] = useState<string | null>(null)

  const openWorkspace = async (platform: Platform): Promise<void> => {
    try {
      setActionError(null)
      await window.quantScreenTrader.openWorkspace(platform)
    } catch {
      setActionError(`Could not open the ${PLATFORM_DETAILS[platform].name} workspace.`)
    }
  }

  return (
    <main className="app-shell dashboard">
      <header className="page-header">
        <div>
          <p className="eyebrow">LOCAL ANALYSIS DESKTOP</p>
          <h1>QuantScreen Trader</h1>
          <p className="page-subtitle">
            Two isolated workspaces, one measured view of market conditions.
          </p>
        </div>
        <div className="phase-chip">SESSIONS · ASSETS · CALIBRATION</div>
      </header>

      <section className="platform-grid" aria-label="Platform workspaces">
        {PLATFORMS.map((platform) => {
          const details = PLATFORM_DETAILS[platform]
          return (
            <article className="platform-card" key={platform}>
              <div className="platform-card__heading">
                <div className={`platform-mark platform-mark--${platform}`} aria-hidden="true">
                  {details.shortName}
                </div>
                <div>
                  <h2>{details.name}</h2>
                  <span className="platform-state">MANUAL LOGIN · ISOLATED SESSION</span>
                </div>
              </div>

              <dl className="metric-grid">
                <div>
                  <dt>Slots</dt>
                  <dd>9 / 9</dd>
                </div>
                <div>
                  <dt>Paper trades today</dt>
                  <dd>—</dd>
                </div>
                <div>
                  <dt>Current win rate</dt>
                  <dd>—</dd>
                </div>
                <div>
                  <dt>Current P/L</dt>
                  <dd>—</dd>
                </div>
              </dl>

              <button className="primary-button" onClick={() => void openWorkspace(platform)}>
                Open {details.name} workspace
              </button>
            </article>
          )
        })}
      </section>

      {actionError ? <p className="error-banner">{actionError}</p> : null}

      <section className="health-panel" aria-labelledby="system-health-title">
        <div className="health-panel__heading">
          <div>
            <p className="eyebrow">LOCAL SERVICE</p>
            <h2 id="system-health-title">System health</h2>
          </div>
          <EngineStatus health={engineHealth} />
        </div>
        <dl className="health-grid">
          <div>
            <dt>Quant engine</dt>
            <dd>{engineHealth.engine?.version ?? 'Unavailable'}</dd>
          </div>
          <div>
            <dt>Database</dt>
            <dd>{engineHealth.engine?.database.toUpperCase() ?? 'UNKNOWN'}</dd>
          </div>
          <div>
            <dt>Latency</dt>
            <dd>{engineHealth.latencyMs === undefined ? '—' : `${engineHealth.latencyMs} ms`}</dd>
          </div>
          <div>
            <dt>Last heartbeat</dt>
            <dd>{engineHealth.engine?.timestamp ?? 'Waiting for engine'}</dd>
          </div>
        </dl>
        {engineHealth.message ? <p className="health-message">{engineHealth.message}</p> : null}
      </section>

      <footer className="dashboard-actions">
        <button disabled title="Analysis controls arrive with the quant pipeline.">
          Pause all
        </button>
        <button disabled title="Analysis controls arrive with the quant pipeline.">
          Stop analysis
        </button>
        <span>Controls unlock in a later phase.</span>
      </footer>
    </main>
  )
}
