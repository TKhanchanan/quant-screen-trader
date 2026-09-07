import { describe, expect, it, vi } from 'vitest'
import {
  PlatformWindowRegistry,
  type ManagedWindow
} from '../electron/main/window-registry'

class FakeWindow implements ManagedWindow {
  readonly focus = vi.fn()
  private destroyed = false
  private closedListener: (() => void) | undefined

  isDestroyed(): boolean {
    return this.destroyed
  }

  once(event: 'closed', listener: () => void): this {
    if (event === 'closed') this.closedListener = listener
    return this
  }

  close(): void {
    this.destroyed = true
    this.closedListener?.()
  }
}

describe('PlatformWindowRegistry', () => {
  it('keeps both platform windows open concurrently', () => {
    const registry = new PlatformWindowRegistry(() => new FakeWindow())

    const capitalBear = registry.open('capitalbear')
    const iqOption = registry.open('iqoption')

    expect(registry.size).toBe(2)
    expect(capitalBear).not.toBe(iqOption)
  })

  it('focuses an existing platform window instead of duplicating it', () => {
    const registry = new PlatformWindowRegistry(() => new FakeWindow())
    const first = registry.open('capitalbear')

    expect(registry.open('capitalbear')).toBe(first)
    expect(first.focus).toHaveBeenCalledOnce()
    expect(registry.size).toBe(1)
  })

  it('closing one platform does not remove the other', () => {
    const registry = new PlatformWindowRegistry(() => new FakeWindow())
    const capitalBear = registry.open('capitalbear')
    const iqOption = registry.open('iqoption')

    capitalBear.close()

    expect(registry.get('capitalbear')).toBeUndefined()
    expect(registry.get('iqoption')).toBe(iqOption)
    expect(registry.size).toBe(1)
  })
})
