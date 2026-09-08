# QuantScreen Trader

QuantScreen Trader is a cross-platform Electron desktop application with a local Python quantitative engine. It includes independent embedded CapitalBear and IQ Option browsers, nine user-configured asset slots per platform, reusable asset presets, normalized drag/resize calibration profiles, local engine health monitoring, and SQLite persistence. It now includes calibrated DOM/visual observation, local OCR, provenance and quality gates, plus a Python one-second and multi-timeframe market-data builder with Parquet storage.

This software is for research and simulation. It does not place unattended real-money orders, collect platform passwords, or bypass platform security controls.

## Requirements

- macOS 13+ on Apple Silicon, or Windows 10/11 x86-64
- [Git](https://git-scm.com/)
- [Node.js 22.12 or newer](https://nodejs.org/) with npm 10 or newer
- [Python 3.12 or newer](https://www.python.org/)

## Set up on macOS

```bash
git clone https://github.com/TKhanchanan/quant-screen-trader.git
cd quant-screen-trader
npm ci
python3 -m venv services/quant-engine/.venv
services/quant-engine/.venv/bin/python -m pip install --upgrade pip
services/quant-engine/.venv/bin/python -m pip install -e "services/quant-engine[dev]"
cp .env.example .env
npm run dev
```

The Electron main process starts the quant engine automatically. If automatic startup is unavailable, run `npm run engine:dev` in one terminal and `npm run dev` in another.
The first `npm run dev` downloads the platform-specific Electron binary; subsequent starts reuse it.

## Set up on Windows

Run these commands in PowerShell:

```powershell
git clone https://github.com/TKhanchanan/quant-screen-trader.git
Set-Location quant-screen-trader
npm ci
py -3.12 -m venv services/quant-engine/.venv
& ".\services\quant-engine\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\services\quant-engine\.venv\Scripts\python.exe" -m pip install -e "services/quant-engine[dev]"
Copy-Item .env.example .env
npm run dev
```

The same `npm run engine:dev` fallback applies on Windows. The root Python runner finds the project virtual environment on both platforms; set `QST_PYTHON_EXECUTABLE` only when a different interpreter is required.
The first `npm run dev` downloads the platform-specific Electron binary; subsequent starts reuse it.

## Validate the project

Run the same commands locally that continuous integration runs:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

The root scripts validate all TypeScript workspaces and the Python service. See [Development](docs/development.md) for individual commands and troubleshooting.

## Project layout

```text
apps/desktop/              Electron, React, and workspace windows
services/quant-engine/     FastAPI service, health stream, and SQLite storage
packages/shared-types/     Shared TypeScript contracts and runtime schemas
tests/                     Repository-level test assets when needed
docs/                      Architecture and development documentation
.github/workflows/         Continuous integration
```

## Local configuration and data

`.env.example` contains safe defaults only. Copy it to the ignored `.env` file for local overrides. Do not put credentials, cookies, tokens, browser profiles, or session data in the repository.

Runtime data is created outside the checkout using the operating system application-data directory:

- macOS: `~/Library/Application Support/QuantScreenTrader/`
- Windows: `%APPDATA%\QuantScreenTrader\`

SQLite databases, browser sessions, logs, screenshots, market datasets, diagnostic exports, and generated model artifacts are intentionally ignored by Git.

## Status

Phase 0 ✅ Repository foundation

Phase 1 ✅ Application shell

Phase 2 ✅ Isolated embedded sessions; login is manual and authentication state remains unverified.

Phase 3 ✅ Asset configuration, presets, and calibration. See the verification notes and limitations in the guides below.

Phase 4 capture/parser implementation is available; authenticated broker extraction acceptance is pending. Phase 5 data-builder tests pass. Signal generation, trading and installer packaging remain unimplemented.

## Workspace workflow

1. Open either or both platform workspaces. Log in manually inside each platform's browser.
2. Open **Asset Setup**, enter your own asset labels, enable the desired slots, and save. This does not change the platform's instruments; select those manually on the platform.
3. Create a named asset preset for reuse. Presets never cross platform boundaries.
4. Open **Calibrate Slots**. Drag the slot labels and resize with the lower-right handles. Create a profile, or save over the selected profile. Cancel discards unsaved geometry.

See [Platform sessions](docs/platform-session.md), [Calibration](docs/calibration.md), and [Asset presets](docs/asset-presets.md).

Contributions follow [Conventional Commits](CONTRIBUTING.md). The design and process boundaries are documented in [Architecture](docs/architecture.md).


## Market data foundation

Load an asset configuration and calibration profile, then choose **Start observation** in a workspace. Disabled slots stay idle. Observation status, source, quality, age and series progress appear on each slot. Sampling targets are configurable per workspace; developer diagnostics expose timings, ROI and queue drops. Images are never stored.

Phase 4 implementation is available with account-free native OCR and scheduler tests. Authenticated broker selector/OCR accuracy still requires live verification; ambiguous chart text produces DATA_UNCERTAIN rather than a fabricated price. Phase 4 is not marked complete pending that acceptance check.

Phase 5 ✅ Deterministic samples, one-second selection, S5/M1/M5/M10 OHLC, forming/closed state, gaps, bounded buffers and Parquet/replay tests. Phase 6 has not started.

See [Providers](docs/market-data-providers.md), [Capture and parsing](docs/capture-parser.md), [Market data builder](docs/market-data-builder.md) and [Data quality](docs/data-quality.md). After updating, install the Python dependencies again to obtain DuckDB: `node scripts/python.mjs -m pip install -e "services/quant-engine[dev]"`.


The interrupted temporary-profile acceptance check is complete, including both unauthenticated workspaces, idle disabled slots, calibration pause and 18-stream synthetic HTTP/Parquet validation. Real authenticated broker extraction remains pending for both platforms. See the [2026-09-08 verification results](docs/development.md#acceptance-continuation--2026-09-08).


## Asset synchronization

Workspaces now offer **Sync Assets** and **Auto Sync Assets**, with AUTO/MANUAL locks in Asset Setup. Confident, correctly mapped detections populate and enable AUTO slots; uncertain reads preserve existing values. Auto changes create per-slot series boundaries. See [Asset detection](docs/asset-detection.md) for mapping, confidence and debounce rules.

The inspected live broker pages render their trading UI into a single canvas. Their current default full-browser grid must be aligned with chart regions before safe OCR synchronization; authenticated asset/price extraction is still pending. DOM fixture success does not establish support for these canvas layouts.
