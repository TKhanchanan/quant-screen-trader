import { useEffect, useRef, useState, type JSX } from 'react'
import type { Platform } from '@quant-screen-trader/shared-types'

interface WorkspaceProps {
  platform: Platform
}

export function Workspace({ platform }: WorkspaceProps): JSX.Element {
  const [actionError, setActionError] = useState('')
  const region = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const element = region.current
    if (!element) return
    const layout = (): void => {
      const rect = element.getBoundingClientRect()
      const x = Math.ceil(rect.x), y = Math.ceil(rect.y)
      const width = Math.min(Math.floor(rect.right), window.innerWidth) - x
      const height = Math.min(Math.floor(rect.bottom), window.innerHeight) - y
      if (width < 1 || height < 1) return
      void window.quantScreenTrader.platformCommand({ operation: 'layout', platform,
        bounds: { x, y, width, height }, visible: true }).catch(() => setActionError('Browser layout unavailable; resize the window to retry.'))
    }
    const observer = new ResizeObserver(layout)
    observer.observe(element); layout()
    window.addEventListener('resize', layout)
    return () => { observer.disconnect(); window.removeEventListener('resize', layout) }
  }, [platform])

  return <main className="workspace-shell">
    {actionError && <p role="alert" className="error-banner">{actionError}</p>}
    <div ref={region} className="browser-region browser-region--full" />
  </main>
}
