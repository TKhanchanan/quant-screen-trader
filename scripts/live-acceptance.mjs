/**
 * Opt-in live acceptance runner. Never runs automatically and never sends input events
 * to a broker page: the probes only capture the visible surface and read it.
 *
 *   node scripts/live-acceptance.mjs --platform iqoption --stage inspect --out <dir>
 */
import { build } from 'esbuild'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir, homedir } from 'node:os'
import { join, resolve } from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const argument = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`)
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback
}
const platform = argument('platform', 'iqoption')
const stage = argument('stage', 'inspect')
const out = resolve(argument('out', join(tmpdir(), `qst-live-${platform}-${stage}`)))
const entry = { inspect: 'apps/desktop/scripts/live-inspect.ts', analyze: 'apps/desktop/scripts/live-analyze.ts',
  pipeline: 'apps/desktop/scripts/live-pipeline.ts' }[stage]
if (!entry) throw new Error(`Unknown stage: ${stage}`)

const directory = mkdtempSync(join(tmpdir(), 'qst-live-build-'))
mkdirSync(out, { recursive: true })
try {
  const target = join(directory, 'live.cjs')
  await build({
    entryPoints: [entry], outfile: target, bundle: true, platform: 'node', format: 'cjs',
    external: ['electron'],
    plugins: [{ name: 'local-ocr', setup(b) { b.onResolve({ filter: /^tesseract\.js$/ }, () => ({ path: require.resolve('tesseract.js'), external: true })) } }],
    alias: { '@quant-screen-trader/shared-types': resolve('packages/shared-types/src/index.ts') }
  })
  const env = {
    ...process.env,
    NODE_PATH: resolve('node_modules'),
    QST_LIVE_PLATFORM: platform,
    QST_LIVE_STAGE: stage,
    QST_LIVE_OUT: out,
    QST_LIVE_USER_DATA: argument('user-data', join(homedir(), 'Library/Application Support/QuantScreenTrader'))
  }
  for (const name of ['settle-ms', 'width', 'height', 'header', 'observe-ms', 'interval-ms', 'image', 'resize-width', 'resize-height', 'zoom']) {
    const value = argument(name)
    if (value) env[`QST_LIVE_${name.replace(/-/g, '_').toUpperCase()}`] = value
  }
  delete env.ELECTRON_RUN_AS_NODE
  const result = spawnSync(require('electron'), [target], { env, stdio: 'inherit', timeout: Number(argument('timeout-ms', 600000)) })
  console.log(`\nArtifacts: ${out}`)
  process.exitCode = result.status ?? 1
} finally {
  rmSync(directory, { recursive: true, force: true })
}
