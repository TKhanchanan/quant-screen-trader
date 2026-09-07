import { PlatformSchema } from '@quant-screen-trader/shared-types'
import type { JSX } from 'react'
import { Dashboard } from './components/Dashboard'
import { Workspace } from './components/Workspace'
import { useEngineHealthPolling } from './hooks/useEngineHealthPolling'

export function App(): JSX.Element {
  useEngineHealthPolling()

  const query = new URLSearchParams(window.location.search)
  const platform = PlatformSchema.safeParse(query.get('platform'))

  if (query.get('view') === 'workspace' && platform.success) {
    return <Workspace platform={platform.data} />
  }

  return <Dashboard />
}
