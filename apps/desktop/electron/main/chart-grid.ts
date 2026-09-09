import { defaultChartGrid, deriveChartGrid, type ChartGridGeometry, type NormalizedBounds,
  type Platform } from '@quant-screen-trader/shared-types'

interface BrowserDimensions { width: number; height: number }

abstract class AutoChartGridResolver {
  abstract readonly platform: Platform
  resolve(dimensions: BrowserDimensions): ChartGridGeometry {
    if (!Number.isFinite(dimensions.width) || !Number.isFinite(dimensions.height) || dimensions.width < 600 || dimensions.height < 360)
      throw new Error('Browser surface is too small to resolve the 3×3 chart grid')
    return defaultChartGrid(this.platform)
  }
}

export class CapitalBearChartGridResolver extends AutoChartGridResolver {
  readonly platform = 'capitalbear' as const
}

export class IQOptionChartGridResolver extends AutoChartGridResolver {
  readonly platform = 'iqoption' as const
}

export class ManualChartGridResolver {
  constructor(private readonly platform: Platform) {}
  resolve(bounds: NormalizedBounds): ChartGridGeometry { return deriveChartGrid(this.platform, bounds, 'MANUAL') }
}

export function chartGridResolver(platform: Platform): CapitalBearChartGridResolver | IQOptionChartGridResolver {
  return platform === 'capitalbear' ? new CapitalBearChartGridResolver() : new IQOptionChartGridResolver()
}
