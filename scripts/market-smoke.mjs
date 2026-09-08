import { build } from 'esbuild'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const directory = mkdtempSync(join(tmpdir(), 'qst-market-build-'))
try {
  const target = join(directory, 'smoke.cjs')
  const profile = join(directory, 'profile')
  mkdirSync(profile)
  await build({ entryPoints: [process.argv.includes('--assets') ? 'apps/desktop/scripts/asset-smoke.ts' : 'apps/desktop/scripts/market-smoke.ts'], outfile: target, bundle: true,
    platform: 'node', format: 'cjs', external: ['electron'], plugins: [{ name: 'local-ocr', setup(b) { b.onResolve({ filter: /^tesseract\.js$/ }, () => ({ path: require.resolve('tesseract.js'), external: true })) } }],
    alias: { '@quant-screen-trader/shared-types': resolve('packages/shared-types/src/index.ts') } })
  const env = { ...process.env, NODE_PATH: resolve('node_modules'), QST_SMOKE_DATA_DIR: profile }
  delete env.ELECTRON_RUN_AS_NODE
  const result = spawnSync(require('electron'), [target], { env, stdio: 'inherit', timeout: 60000 })
  process.exitCode = result.status ?? 1
} finally { rmSync(directory, { recursive: true, force: true }) }
