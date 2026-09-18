import { randomUUID } from 'node:crypto'
import { observation } from '../electron/main/market-providers'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Dashboard } from '../src/renderer/src/components/Dashboard'
import { Workspace } from '../src/renderer/src/components/Workspace'
import { PlatformControlWindow, PriceReading } from '../src/renderer/src/components/PlatformControlWindow'

describe('Phase 1 renderer', () => {
  it('shows both platform workspaces on the dashboard', () => {
    const markup = renderToStaticMarkup(createElement(Dashboard))

    expect(markup).toContain('CapitalBear')
    expect(markup).toContain('IQ Option')
    expect(markup).not.toContain('href="#"')
    expect(markup).not.toContain('Local Service Online')
    expect(markup.match(/9 ช่อง/g)).toHaveLength(2)
  })

  it('renders exactly nine slots in each control window', () => {
    for (const platform of ['capitalbear', 'iqoption'] as const) {
      const markup = renderToStaticMarkup(createElement(PlatformControlWindow, { platform }))

      expect(markup.match(/data-slot-id=/g)).toHaveLength(9)
      expect(markup).toContain('ซิงก์สินทรัพย์')
      expect(markup).toContain('เริ่มสังเกตการณ์')
    }
  })

  it('renders a minimal broker workspace with no toolbar', () => {
    for (const platform of ['capitalbear', 'iqoption'] as const) {
      const markup = renderToStaticMarkup(createElement(Workspace, { platform }))

      expect(markup).toContain('browser-region--full')
      expect(markup).not.toContain('workspace-toolbar')
      expect(markup).not.toContain('ซิงก์สินทรัพย์')
      expect(markup).not.toContain('slot-summary')
    }
  })
})

it('renders an uncertain parsed price and exposes the read-only probe', () => {
  const value = observation({ platform: 'iqoption', slotId: 1, assetName: 'EUR/USD', contextId: randomUUID(),
    calibrationProfileId: null, bounds: { x: 0, y: 0, width: 1, height: 1 } }, 'VISUAL',
    { asset: 'EUR/USD', price: '1.15368', confidence: .62 }, Date.now())
  const markup = renderToStaticMarkup(createElement(PriceReading, { observation: value }))
  expect(markup).toContain('1.15368'); expect(markup).toContain('UNCERTAIN'); expect(markup).toContain('62%')
  expect(renderToStaticMarkup(createElement(PlatformControlWindow, { platform: 'iqoption' }))).toContain('ตรวจสอบราคา')
})
