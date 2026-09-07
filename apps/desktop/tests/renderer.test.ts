import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Dashboard } from '../src/renderer/src/components/Dashboard'
import { Workspace } from '../src/renderer/src/components/Workspace'

describe('Phase 1 renderer', () => {
  it('shows both platform workspaces on the dashboard', () => {
    const markup = renderToStaticMarkup(createElement(Dashboard))

    expect(markup).toContain('CapitalBear')
    expect(markup).toContain('IQ Option')
    expect(markup.match(/9 \/ 9/g)).toHaveLength(2)
  })

  it('renders exactly nine slots in each workspace', () => {
    for (const platform of ['capitalbear', 'iqoption'] as const) {
      const markup = renderToStaticMarkup(createElement(Workspace, { platform }))

      expect(markup.match(/data-slot-id=/g)).toHaveLength(9)
    }
  })
})
