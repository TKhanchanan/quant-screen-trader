import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, lstatSync, realpathSync, writeFileSync } from 'node:fs';
import { resolve, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { listPackage } from '@electron/asar';

const root = fileURLToPath(new URL('../', import.meta.url));
process.chdir(root);
const platform = process.platform, arch = process.arch;
const target = platform === 'darwin' ? 'mac' : 'win';
const version = JSON.parse(readFileSync('package.json')).version;
const python = resolve('services/quant-engine/.venv-package', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const engine = resolve('build/engine/quant-engine', platform === 'win32' ? 'quant-engine.exe' : 'quant-engine');
export function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: 'inherit', ...options });
  if (result.error) throw result.error;
  assert.equal(result.status, 0, `${command} failed (${result.status})`);
}
function capture(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8' });
  if (result.error) throw result.error;
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}
function nativeTarget() {
  assert.ok((platform === 'darwin' && arch === 'arm64') || (platform === 'win32' && arch === 'x64'),
    'Build natively on macOS arm64 or Windows x64; cross-packaging is not supported.');
  assert.equal(JSON.parse(readFileSync('apps/desktop/package.json')).version, version, 'Version mismatch');
}
export function binaryArchitecture(file) {
  const data = readFileSync(file);
  if (data.readUInt32LE(0) === 0xfeedfacf) {
    const cpu = data.readUInt32LE(4);
    assert.ok(cpu === 0x100000c || cpu === 0x1000007, 'Unsupported Mach-O CPU');
    return cpu === 0x100000c ? 'arm64' : 'x64';
  }
  assert.equal(data.toString('ascii', 0, 2), 'MZ', `Not a supported native binary: ${file}`);
  const pe = data.readUInt32LE(0x3c);
  assert.equal(data.toString('ascii', pe, pe + 4), 'PE\0\0');
  const machine = data.readUInt16LE(pe + 4);
  assert.ok(machine === 0x8664 || machine === 0xaa64, `Unsupported PE machine: ${machine}`);
  return machine === 0x8664 ? 'x64' : 'arm64';
}
// Inspect both ASAR names and actual resource files; application inputs are allowlisted above.
export function assertPublicFiles(files) {
  const forbidden = /(^|\/)(\.env(?:\..*)?|\.venv(?:-package)?|venv|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|coverage|\.coverage.*|screenshots|captures|logs|browser-profiles?|user-data|session-data|local-data|market-data|Local Storage|Session Storage|IndexedDB|Cookies(?:-journal|\.json)?|(?:capitalbear|iqoption)-profile|credentials\.json|tokens\.json|secrets\.json|auth\.json|sessions?\.json|storage-state\.json|session-state\.json|calibrations?\.json|asset-presets\.json)(\/|$)|\.(sqlite3?|db|duckdb)(?:[-.].*)?$|\.(parquet|log|har|pem|key|p12|pfx)$/i;
  for (const file of files) assert.ok(!forbidden.test(file.replaceAll('\\', '/')), `Private/runtime file in package: ${file}`);
}
function walk(directory, base = directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const file = join(directory, entry.name), name = relative(base, file).split(sep).join('/');
    if (lstatSync(file).isSymbolicLink()) {
      const dest = relative(realpathSync(base), realpathSync(file));
      assert.ok(dest !== '..' && !dest.startsWith(`..${sep}`), `Resource symlink escapes bundle: ${name}`);
      return [name];
    }
    return entry.isDirectory() ? [name, ...walk(file, base)] : [name];
  });
}
function manifest() {
  return { appVersion: version, commitSha: capture('git', ['rev-parse', 'HEAD']), platform, arch,
    engineBundled: true, engineRelativePath: `quant-engine/quant-engine${platform === 'win32' ? '.exe' : ''}`,
    packageTarget: platform === 'darwin' ? 'dmg' : 'nsis', signed: false };
}
function engineBuild() {
  nativeTarget();
  run(process.execPath, ['scripts/python.mjs', '-m', 'venv', 'services/quant-engine/.venv-package']);
  run(python, ['-m', 'pip', 'install', '-r', 'packaging/requirements.txt']);
  assert.equal(capture(python, ['-c', 'import platform; print(platform.machine().lower())']), platform === 'darwin' ? 'arm64' : 'amd64');
  mkdirSync('build/pyinstaller', { recursive: true });
  run(python, ['-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--noupx',
    '--name', 'quant-engine', '--distpath', 'build/engine', '--workpath', 'build/pyinstaller/work',
    '--specpath', 'build/pyinstaller', '--paths', resolve('services/quant-engine/src'),
    '--collect-submodules', 'quant_engine', '--collect-submodules', 'uvicorn',
    ...readdirSync('services/quant-engine/src/quant_engine/storage/migrations')
      .filter(name => /^\d{4}_[a-z_]+\.sql$/.test(name)).sort()
      .flatMap(name => ['--add-data', `${resolve('services/quant-engine/src/quant_engine/storage/migrations', name)}:quant_engine/storage/migrations`]),
    '--hidden-import', '_duckdb', '--exclude-module', 'pytest', '--exclude-module', 'psutil',
    ...(platform === 'darwin' ? ['--target-arch', 'arm64'] : []), 'packaging/engine-entry.py'],
  { env: { ...process.env, PYTHONPATH: resolve('services/quant-engine/src'), PYTHONHASHSEED: '0', SOURCE_DATE_EPOCH: capture('git', ['show', '-s', '--format=%ct', 'HEAD']) } });
  assert.equal(binaryArchitecture(engine), arch);
  writeFileSync('build/engine/build-metadata.json', JSON.stringify(manifest(), null, 2) + '\n');
  run(python, ['packaging/smoke.py', 'engine', engine, version]);
}
export function packagePaths() {
  const app = resolve(platform === 'darwin' ? 'release/mac-arm64/QuantScreenTrader.app' : 'release/win-unpacked');
  const resources = join(app, platform === 'darwin' ? 'Contents/Resources' : 'resources');
  const executable = join(app, platform === 'darwin' ? 'Contents/MacOS/QuantScreenTrader' : 'QuantScreenTrader.exe');
  return { app, resources, executable };
}
function verify() {
  nativeTarget();
  const { app, resources, executable } = packagePaths();
  const metadata = JSON.parse(readFileSync(join(resources, 'package-manifest.json')));
  assert.equal(metadata.appVersion, version);
  assert.match(metadata.commitSha, /^[0-9a-f]{40}$/);
  assert.equal(capture('git', ['cat-file', '-t', metadata.commitSha]), 'commit', 'Unknown package source commit');
  assert.equal(metadata.platform, platform);
  assert.equal(metadata.arch, arch);
  assert.equal(metadata.engineBundled, true);
  assert.equal(metadata.engineRelativePath, manifest().engineRelativePath);
  assert.equal(metadata.packageTarget, manifest().packageTarget);
  const binary = join(resources, metadata.engineRelativePath);
  assert.equal(binaryArchitecture(executable), arch);
  assert.equal(binaryArchitecture(binary), arch);
  const archived = listPackage(join(resources, 'app.asar')).map(name => name.replace(/^\//, ''));
  assert.ok(!archived.some(name => name.startsWith('quant-engine/')), 'Engine must be outside ASAR');
  const files = walk(app);
  assertPublicFiles([...files, ...archived]);
  for (const suffix of ['node_modules/tesseract.js/src/worker-script/node/index.js',
    'node_modules/tesseract.js-core/tesseract-core-lstm.wasm',
    'node_modules/@tesseract.js-data/eng/4.0.0/eng.traineddata.gz']) {
    assert.ok(existsSync(join(resources, 'app.asar.unpacked', suffix)), `Offline OCR resource missing: ${suffix}`);
  }
  run(python, ['packaging/smoke.py', 'engine', binary, version]);
  // Run OCR inside the real installed Electron entry point, with a temporary profile and no broker navigation.
  run(python, ['packaging/smoke.py', 'app', executable, version]);
  const artifact = `QuantScreenTrader-${version}-${platform === 'darwin' ? 'macos' : 'windows'}-${arch}.${platform === 'darwin' ? 'dmg' : 'exe'}`;
  assert.ok(readFileSync(join('release', artifact)).length > 0, 'Installer missing');
  const signed = platform === 'darwin'
    ? /Authority=Developer ID Application/.test(spawnSync('codesign', ['-dv', app], { encoding: 'utf8' }).stderr ?? '')
    : capture('powershell.exe', ['-NoProfile', '-Command', `(Get-AuthenticodeSignature -LiteralPath '${executable.replaceAll("'", "''")}').Status`]) === 'Valid';
  assert.equal(metadata.signed, signed, 'Manifest signing status differs from actual application signature');
  writeFileSync('release/package-manifest.json', JSON.stringify(metadata, null, 2) + '\n');
  writeFileSync('release/package-files.txt', [...files, ...archived.map(f => `app.asar/${f}`)].sort().join('\n') + '\n');
  const checksumFiles = [artifact, 'package-manifest.json', 'package-files.txt'];
  writeFileSync('release/SHA256SUMS.txt', checksumFiles.map(file => `${createHash('sha256').update(readFileSync(join('release', file))).digest('hex')}  ${file}`).join('\n') + '\n');
  console.info(`Verified ${artifact}: ${arch}, native engine, offline OCR, lifecycle, private file exclusion, SHA-256.`);
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const action = process.argv[2];
  if (action === 'engine') engineBuild();
  else if (action === 'verify') verify();
  else if (action === 'mac' || action === 'win') {
    nativeTarget();
    assert.equal(action, target, 'Installer must be built on its native OS/architecture');
    engineBuild();
    const metadata = manifest();
    // Signing credentials are optional and remain under electron-builder's normal control.
    metadata.signed = Boolean(process.env.CSC_LINK || process.env.CSC_NAME || process.env.WIN_CSC_LINK || process.env.CSC_IDENTITY_AUTO_DISCOVERY === 'true');
    writeFileSync('build/package-manifest.json', JSON.stringify(metadata, null, 2) + '\n');
    run(process.execPath, [process.env.npm_execpath, 'run', 'build']);
    run(process.execPath, ['node_modules/electron-builder/cli.js', `--${action}`, `--${arch}`, '--config', 'packaging/electron-builder.cjs', '--publish', 'never'],
      { env: { ...process.env, CSC_IDENTITY_AUTO_DISCOVERY: process.env.CSC_IDENTITY_AUTO_DISCOVERY ?? (metadata.signed ? 'true' : 'false') } });
    verify();
  } else throw new Error('Usage: node scripts/package.mjs engine|mac|win|verify');
}
