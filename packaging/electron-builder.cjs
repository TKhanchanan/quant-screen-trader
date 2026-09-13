module.exports = {
  appId: 'com.tkhanchanan.quantscreentrader',
  productName: 'QuantScreenTrader',
  directories: { app: 'apps/desktop', output: 'release' },
  asar: true,
  // OCR worker threads and WASM must use real, offline resource files.
  asarUnpack: ['node_modules/**'],
  files: ['out/**', 'package.json', '!**/*.map', '!node_modules/@quant-screen-trader/**'],
  extraResources: [
    { from: 'build/engine/quant-engine', to: 'quant-engine', filter: ['**/*'] },
    { from: 'build/package-manifest.json', to: 'package-manifest.json' }
  ],
  npmRebuild: false,
  publish: null,
  mac: {
    target: [{ target: 'dmg', arch: ['arm64'] }],
    category: 'public.app-category.finance',
    artifactName: 'QuantScreenTrader-${version}-macos-${arch}.${ext}'
  },
  win: {
    target: [{ target: 'nsis', arch: ['x64'] }],
    artifactName: 'QuantScreenTrader-${version}-windows-${arch}.${ext}'
  },
  nsis: { oneClick: false, perMachine: false, allowToChangeInstallationDirectory: true,
    deleteAppDataOnUninstall: false, runAfterFinish: false }
}
