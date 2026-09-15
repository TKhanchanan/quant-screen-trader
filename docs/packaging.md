# Desktop packaging (Phase 13)

QuantScreenTrader 0.1.0 packages Electron and a platform-native Python engine. The
application version is independent of the frozen `qfe-*` / `qst-*` contracts.
Phase 14 implementation is present; live acceptance is pending. Packaging does not arm
execution or navigate a broker.

## Build and verification

Build on **Apple Silicon macOS arm64** or **Windows x64**, with Node 22.12+ and
Python 3.14 installed on the *build machine*. The installed application needs no
Python, pip, Node, npm, virtual environment, or runtime internet download.

```sh
npm ci
python -m pip install -e "services/quant-engine[dev]"
npm run lint
npm run typecheck
npm test
npm run build
npm run package:mac     # Apple Silicon only
# npm run package:win   # Windows x64 only
npm run package:verify # Recheck this checkout's existing native artifact
```

`npm run package:engine` builds and tests just the native engine. The scripts
create an isolated `services/quant-engine/.venv-package` from the fully pinned
`packaging/requirements.txt`; the ordinary development `.venv` and
`QST_PYTHON_EXECUTABLE` override remain supported. Dependencies are installed at
build time. PyInstaller onedir preserves native DuckDB, FastAPI and Uvicorn
imports and dynamic libraries. `packaging/engine-entry.py` calls the unchanged
`quant_engine.__main__:main` CLI. Run with a native Python interpreter; Node,
Python, engine and Electron architectures are checked and mismatches fail.

The pinned toolchain, dependency lockfile, fixed Python hash seed and commit
source epoch make build inputs repeatable. Installers are not promised to be
byte-identical across machines, SDK versions or signing runs. Use their SHA-256
checksums to identify the actual delivered bytes.

Development still uses electron-vite and interpreter-launched Uvicorn.
Packaged Electron uses only these existing paths:

- macOS: `QuantScreenTrader.app/Contents/Resources/quant-engine/quant-engine`
- Windows: `<install>/resources/quant-engine/quant-engine.exe`

The entire onedir engine is copied with `extraResources`, outside `app.asar`.
Only built Electron output, production dependencies and package metadata enter
ASAR. electron-vite still writes `apps/desktop/out`; installers go to `release`.

Artifacts:

- `QuantScreenTrader-0.1.0-macos-arm64.dmg`
- `QuantScreenTrader-0.1.0-windows-x64.exe` (offline NSIS installer)
- `package-manifest.json`: app version, source SHA, platform, architecture,
  engine path, target and verified signing status; no local absolute paths
- `package-files.txt`: inspected package and ASAR file inventory
- `SHA256SUMS.txt`: hashes of the installer, manifest and inventory

Verify on macOS with `shasum -a 256 -c SHA256SUMS.txt`. On Windows,
`Get-FileHash -Algorithm SHA256 .\QuantScreenTrader-0.1.0-windows-x64.exe`
should match the corresponding entry. Keep each platform's checksum file with
its corresponding artifacts.

## Install, data and upgrades

macOS: open the DMG, drag QuantScreenTrader to Applications, then launch it.
Windows: run the NSIS installer, select the installation location, then launch
QuantScreenTrader from its shortcut. Installation does not launch or arm it.
Quit before replacing an installed build. Updates are manual; there is no updater,
release server, silent downloader or background installer.

Mutable data lives under the stable OS directory:

- macOS: `~/Library/Application Support/QuantScreenTrader/`
- Windows: `%APPDATA%/QuantScreenTrader/`

Configuration, calibration, asset presets, SQLite (`db/`), Parquet history,
policy journals and persistent browser sessions remain there across reinstalls.
The installer does not contain or overwrite this directory, and NSIS does not
delete it on uninstall. Back it up before manually deleting it. No mutable data
belongs in Applications, Program Files, ASAR or resources. Development may still
use its existing explicit external data root; packaged mode ignores `QST_DATA_DIR`.

A normal Electron single-instance lock prevents a second installed instance from
starting an engine or opening the same persistent profile. It restores/focuses
the dashboard. The normal quit handler waits for engine exit and escalates to a
forced termination after five seconds if needed. A fresh application launch
creates one new engine. Abrupt OS termination/power loss is not equivalent to a
normal quit and is outside this acceptance test.

## Diagnostics, privacy and OCR

The existing health UI reports a missing/crashed engine with its bundled path
and exit reason. Packaged mode never falls back to arbitrary system Python.
Startup records in `logs/startup.log` contain app version, OS, architecture,
engine path, start PID and exit code/signal. Packaged engine stdout/stderr are
not copied into these logs, and environment values, credentials and browser
session contents are never logged by packaging code.

Packaging uses an input allowlist. Verification inspects both physical files
and ASAR members, rejecting obvious `.env` files, virtual environments, caches,
credentials, cookies/profiles, runtime databases, Parquet files, screenshots and
logs. Local data is never an installer input. This is a guard against accidental
inclusion, not a content-based secret scanner for arbitrary third-party code.

Production Node dependencies are unpacked so Tesseract worker scripts, their
transitive dependencies, WASM cores and English traineddata all use real files. Packaged resource resolution uses the application path,
not the working directory. OCR initialization and recognition are exercised from
the packaged application with a temporary working directory and local traineddata;
there is no production CDN dependency.

`package:verify` runs the bundled binary's `--help`, rejects `0.0.0.0`, starts on
an unused `127.0.0.1` port and verifies the health contract and writable SQLite.
It checks shutdown, orphan descendants and restart/data preservation twice.
It also launches the actual Electron entry point, verifies rendered content,
initializes OCR, checks a second launch, quits through `app.quit()` and relaunches.
The explicit `--qst-package-smoke` switch uses a temporary app-data root supplied
by the harness. It never touches an existing profile, starts observation, opens
a broker or changes execution state. Child PATH excludes development Python.

## CI and signing

`.github/workflows/package.yml` is a separate **workflow_dispatch** workflow.
It leaves normal CI unchanged and never publishes a GitHub Release. Native
`macos-15` arm64 and `windows-latest` x64 jobs install dependencies, run the full
validation suite, build installers, verify packages and upload checksummed
artifacts. Runtime architecture checks fail rather than mislabel an artifact.
GitHub documents the available [native runner architectures](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
If an arm64 runner is unavailable for this repository, that job cannot count as
accepted; local Apple Silicon acceptance is still required.

Unsigned builds are supported. macOS may apply an ad-hoc signature for arm64
execution; this does **not** mean Developer ID signing/notarization and is reported
as `signed: false`. Unsigned/not notarized DMGs can trigger Gatekeeper. Unsigned
Windows installers can trigger SmartScreen. Packaging success does not establish
production signing acceptance. No fake identity or secret is supplied. Normal
electron-builder `CSC_LINK`, `CSC_NAME`, `CSC_KEY_PASSWORD` and Windows signing
variables remain available; verification compares the declared signing status
with the actual application signature. Signing and notarization require the
owner's real credentials and separate distribution acceptance.

## Acceptance record and limitations

Local macOS acceptance passed for implementation commit
`1903fdb3768b3eb61a2512ffe0f2cfd16ec085b3`:

- DMG: `QuantScreenTrader-0.1.0-macos-arm64.dmg`, 196,397,071 bytes.
- SHA-256: `e1a28eeb9bff803e46598184dc28c839760f353e7fedfca731a5019dbb101418`.
- Both Electron and the bundled engine were inspected as native arm64 binaries.
- Engine health, writable/preserved data, offline OCR, rendered dashboard,
  single-instance protection, normal quit and relaunch passed. No engine remained.
- The application also passed two launches from the actual read-only mounted DMG.
- Package inventory/private-data checks and all three checksum entries passed.
- Local lint, typecheck, build, 248 JavaScript tests, 838 Python tests and two
  packaging checks passed. No protected engine or execution source changed.
- [Normal CI for that commit](https://github.com/TKhanchanan/quant-screen-trader/actions/runs/34749128011)
  passed all required jobs.

Windows artifacts and packaging CI acceptance are still pending. The connected
GitHub account cannot dispatch the workflow (HTTP 403); an account with repository
write access must push any pending packaging fixes and dispatch `Desktop packages`
on `main`. The packaging workflow explicitly selects setup-python's interpreter
through the existing `QST_PYTHON_EXECUTABLE` override so Windows checks do not fall
back to a different `py -3.12` installation.

Configuration alone does not close Phase 13. The automated
Windows app smoke is a process/renderer/OCR check, not a manual interactive
Windows installation test. No broker or live-market acceptance is performed.
The application currently uses Electron's default icon because the repository
contains no product icon asset. Phase 14 implementation is present; sustained live acceptance
remains pending.
