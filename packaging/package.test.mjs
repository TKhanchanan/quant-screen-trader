import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { assertPublicFiles, binaryArchitecture } from '../scripts/package.mjs';

test('reject private/runtime files in either resource trees or ASAR lists', () => {
  for (const file of ['.env', '.env.production', '.venv/bin/python', 'a/__pycache__/x.pyc',
    'data/x.sqlite3-wal', 'x.parquet', 'resources/screenshots/one.png', 'Cookies', 'Session Storage/state',
    'app/tokens.json', 'cookies.json', 'capitalbear-profile/Preferences', 'iqoption-profile/Preferences', 'calibrations.json', 'asset-presets.json', 'calibration.json', 'private.key', 'a\\credentials.json']) {
    assert.throws(() => assertPublicFiles([file]), /Private\/runtime file/);
  }
  assert.doesNotThrow(() => assertPublicFiles(['node_modules/tesseract.js/src/index.js',
    'quant-engine/_internal/base_library.zip', 'quant-engine/quant-engine.exe', 'package-manifest.json']));
});
test('read native architecture from headers, not artifact labels', () => {
  const directory = mkdtempSync(join(tmpdir(), 'qst-arch-'));
  try {
    const file = join(directory, 'binary');
    const macho = Buffer.alloc(64);
    macho.writeUInt32LE(0xfeedfacf, 0); macho.writeUInt32LE(0x100000c, 4);
    writeFileSync(file, macho); assert.equal(binaryArchitecture(file), 'arm64');
    const pe = Buffer.alloc(128);
    pe.write('MZ'); pe.writeUInt32LE(64, 0x3c); pe.write('PE\0\0', 64); pe.writeUInt16LE(0x8664, 68);
    writeFileSync(file, pe); assert.equal(binaryArchitecture(file), 'x64');
    writeFileSync(file, Buffer.alloc(128)); assert.throws(() => binaryArchitecture(file));
  } finally { rmSync(directory, { recursive: true }); }
});
