# Development

## Toolchain

QuantScreen Trader uses an npm workspace for the Electron/TypeScript code and an editable Python package for the local quant engine. Supported development baselines are Node.js 22.12+, npm 10+, and Python 3.12+.

Use the complete macOS or Windows bootstrap commands in the [README](../README.md). `npm ci` installs exactly the dependency graph recorded in `package-lock.json`; use `npm install` only when intentionally changing dependencies and commit the resulting lockfile.

## Commands

| Command | Purpose |
| --- | --- |
| `npm run dev` | Install the platform Electron binary on first use, start Electron development mode, and start the local engine when needed. |
| `npm run engine:dev` | Start the Python engine with reload for standalone debugging. |
| `npm run engine:start` | Start the Python engine without reload. |
| `npm run lint` | Run workspace linting plus Ruff checks and format verification. |
| `npm run typecheck` | Run TypeScript checks and mypy. |
| `npm test` | Run workspace tests and pytest. |
| `npm run build` | Produce compile-time/build output without committing it. |

The corresponding focused commands are `npm run lint:js`, `npm run lint:python`, `npm run typecheck:js`, `npm run typecheck:python`, `npm run test:js`, and `npm run test:python`.

## Environment

Copy `.env.example` to the ignored `.env` file only when overriding a default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `QST_ENGINE_HOST` | `127.0.0.1` | Loopback address used by the desktop and engine. |
| `QST_ENGINE_PORT` | `8765` | Local HTTP and WebSocket port. |
| `QST_LOG_LEVEL` | `info` | Python engine log level. |
| `QST_DATA_DIR` | OS application-data directory | Standalone engine override; in desktop development, an existing directory outside the checkout overrides both SQLite and Chromium profile storage. |
| `QST_PYTHON_EXECUTABLE` | Project virtual environment, then system Python | Optional interpreter override used by desktop/root scripts. |

Keep the host loopback-only. No credential belongs in `.env`; platform authentication occurs manually inside its isolated embedded-browser profile.

Platform start URL overrides and the strict navigation policy are documented in [Platform sessions](platform-session.md). For destructive UI smoke tests, use a fresh temporary directory and a separate engine port, never the normal user profile:

```bash
mkdir -p /private/tmp/qst-manual-test
QST_DATA_DIR=/private/tmp/qst-manual-test QST_ENGINE_PORT=8877 npm run dev
```

On Windows create a temporary directory outside the checkout and set the same environment variables in PowerShell. The desktop requires the override directory to exist, resolves symlinks and rejects checkout-contained paths. Stop the test instance before deleting its temporary data. Do not use a real login during automated tests.

## Process and health checks

The desktop checks `http://127.0.0.1:8765/health`. Streaming health is available at `ws://127.0.0.1:8765/ws/health`. To inspect the engine independently:

```bash
npm run engine:dev
```

Then request the health endpoint from a separate terminal or browser. Stop the standalone engine before testing desktop-managed startup if the port remains occupied.

## Runtime files

Runtime databases and state live outside the repository:

- macOS: `~/Library/Application Support/QuantScreenTrader/`
- Windows: `%APPDATA%\QuantScreenTrader\`

Never redirect production-like browser profiles, cookies, databases, captures, logs, market data, or model artifacts into a tracked directory. Debug captures are opt-in and remain ignored.

## Testing changes

Tests must be deterministic and must not require a broker login or network access. Add the smallest test that would fail if important logic regresses. Sanitize fixtures; do not commit real screenshots or market datasets.

GitHub Actions runs lint, type checking, tests, and a build on pull requests and pushes to `main`. Reproduce failures with the root commands before changing workflow configuration.

Phase 2/3 tests cover configuration schemas, navigation restrictions, IPC sender scope, independent browser lifecycle/reload/crash behavior, bounds/clamping/scaling, preset/profile persistence and isolation, fresh migration and upgrade from migration 0001. GUI checks additionally exercise both real unauthenticated platform pages and sanitized asset/preset/calibration workflows in an isolated profile. CI does not authenticate with either broker. Windows-specific native compositing and manual authenticated login still require verification on the target machine.

### Phase 2/3 verification — 2026-09-07

On macOS ARM64, Node 26 and Python 3.14: lint, TypeScript/mypy, 49 TypeScript tests, 26 Python tests and production build passed. The existing CI retains its Node 22/Python 3.12 baseline; it has not been rerun remotely for these changes.

Electron GUI input/debugger checks used a separate temporary application-data directory and engine port. Verified both unauthenticated platform documents loaded concurrently, distinct native session objects/storage directories, independent reload and close/reopen, and isolated renderer-crash reporting/recovery. Verified all nine asset fields, enabled/disabled retention, preset CRUD/load, profile CRUD/load, pointer drag/resize, transparent overlay DOM, and full app-restart persistence. Native resize changed both browser and overlay to the same 1050×451 content region; saved normalized geometry remained unchanged and rendered proportionally. Individual renderer captures do not include sibling native views, so these captures are not evidence of full-window compositing on every OS.

## Troubleshooting

- **Python is not found:** create `services/quant-engine/.venv` using the README commands, or set `QST_PYTHON_EXECUTABLE` to a Python 3.12+ executable.
- **Electron starts as Node instead of opening a window:** unset an inherited `ELECTRON_RUN_AS_NODE` variable before running the dev command.
- **Engine health is offline:** run `npm run engine:dev` and read the terminal error. Check that port 8765 is free and that the editable Python package is installed.
- **The database cannot initialize:** verify that the current user can write to the OS application-data directory. Do not move the database into the checkout.
- **`npm ci` rejects the lockfile:** use the Node/npm versions above. If dependencies intentionally changed, run `npm install`, validate, and commit both manifest and lockfile changes.


## Phase 4–5 checks

Reinstall the editable engine dependencies after updating (DuckDB is now required). npm installs Tesseract.js and local English language data. No Python OCR executable, runtime model download or ML model is needed.

- `node scripts/market-smoke.mjs`: real native Electron capture and local OCR of generated synthetic chart text, with an isolated temporary OS profile and no stored screenshot.
- `node scripts/python.mjs services/quant-engine/tests/benchmark_market.py`: 18-slot deterministic builder workload, with wall/CPU time and traced Python memory.
- `npm test`: parser/provider, scheduler isolation/backpressure, quality, time-boundary, no-look-ahead, storage and existing Phase 0–3 regression tests.

On macOS ARM64, the native synthetic OCR check read asset, price, payout and timer at 0.95 reported confidence: capture 188 ms, cold OCR 506 ms, warm OCR 107 ms in one run. The builder processed 21,618 fixture observations in 3.45 seconds (6264/s), 3.43 CPU seconds and 69 MB peak traced Python memory. These are synthetic local measurements, not live broker rates or OCR accuracy. Serial per-platform OCR is the expected bottleneck with nine visual slots; requested sampling targets are not guaranteed.

Authenticated Google-session preservation and broker chart accuracy must be manually checked with both platforms on the target OS. Windows native capture/OCR has not been exercised in this session. Do not mark Phase 4 accepted merely because fixtures pass. Phase 6 remains out of scope.


### Acceptance continuation — 2026-09-08

Started from clean `main` at `a9c8bf162fcbca0703003fd5ffdbc4842ad1409d`. The baseline passed lint, TypeScript/mypy, 77 TypeScript tests, 44 Python tests and production build.

The current 18-slot benchmark processed **21,618 observations in 1.724 seconds** (12,539/s), using 1.723 CPU seconds and 69,069,107 peak traced Python bytes. Each slot retained 1201 samples during the 10-minute simulated workload. These fresh measurements supersede the earlier single-run performance figures for this continuation.

The current native synthetic-text OCR check measured capture **58.5 ms**, cold OCR **423.5 ms**, warm OCR **59.1 ms**, and 0.95 OCR confidence. The harness now removes its own temporary browser profile along with its temporary build directory after Electron exits. No account screenshots are written.

The interrupted temporary-profile verification is complete: both unauthenticated broker pages loaded, each started with nine disabled slots and zero observation work, calibration paused the enabled test slot, and workspace resizing worked. The existing authenticated profiles were not opened or modified. Loopback ingestion accepted 10,818 explicitly SYNTHETIC observations across all 18 slots in 4.66 seconds. Every slot produced 600 one-second records and a closed M10 candle. Normal shutdown flushed storage; typed Parquet reload verified 10,818 observations, 10,818 samples, 10,800 second records and 2394 candles, all with SYNTHETIC provenance. SQLite migrations 1 and 2 were present. Only the test directory `/private/tmp/qst-phase45-gui` was removed after engine shutdown and storage verification.

Continuation regressions found and fixed: capture-rate diagnostics previously combined both platforms, and stop/start could retain stale series progress. Rates are now per workspace/current context; progress clears when the context stops or restarts. Tests additionally confirm ingestion-failure recovery sends fresh observations, bounds the queue, counts drops and isolates a failing slot parser.

Final continuation validation passed lint, TypeScript/mypy, **79 TypeScript tests and 44 Python tests (123 total)**, and production build.

**Authenticated extraction acceptance remains pending for both brokers.** No live price, payout, timer, consecutive OCR accuracy or live S5/M1 candle was verified during these account-free checks. Manual participation is required before using the user's real sessions. Phase 4 remains 🟡; Phase 5's deterministic and synthetic integrated validation passes. Phase 6 has not started.


### Asset synchronization continuation

Migration 0003 adds AUTO/MANUAL modes for slots and presets, preserving existing named slots as MANUAL. Restart the engine to apply it. The workspace and preload gain validated asset-sync commands; both app and engine must be restarted after updating.

`node scripts/market-smoke.mjs --assets` validates the actual rendered-DOM extraction script for both adapters in an isolated Electron profile, without authenticated pages or screenshots. In one run it detected nine generated instruments per adapter, measuring 40.8 ms for the first adapter and 1.9 ms for the second warmed DOM evaluation. These are fixture timings, not live OCR rates.

Live inspection found that both user-opened trading pages expose one full-page canvas and no instrument-label DOM. Both stored calibrations were the unchanged 3×3 browser grid. OCR sync now refuses these unverified regions to avoid assigning toolbar/tab labels to chart slots. Actual broker asset mapping, consecutive price reads and live candles remain pending. No account images were saved during this inspection. Live acceptance requires verified chart-label ROIs.
