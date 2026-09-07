import { build } from 'esbuild'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const directory = mkdtempSync(join(tmpdir(), 'qst-market-build-'))
try {
  const target = join(directory, 'smoke.cjs')
  await build({ entryPoints: ['apps/desktop/scripts/market-smoke.ts'], outfile: target, bundle: true,
    platform: 'node', format: 'cjs', external: ['electron'], plugins: [{ name: 'local-ocr', setup(b) { b.onResolve({ filter: /^tesseract\.js$/ }, () => ({ path: require.resolve('tesseract.js'), external: true })) } }],
    alias: { '@quant-screen-trader/shared-types': resolve('packages/shared-types/src/index.ts') } })
  const env = { ...process.env, NODE_PATH: resolve('node_modules') }
  delete env.ELECTRON_RUN_AS_NODE
  const result = spawnSync(require('electron'), [target], { env, stdio: 'inherit', timeout: 60000 })
  process.exitCode = result.status ?? 1
} finally { rmSync(directory, { recursive: true, force: true }) }
