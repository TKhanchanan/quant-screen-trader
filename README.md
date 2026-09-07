# QuantScreen Trader

QuantScreen Trader is a cross-platform Electron desktop application with a local Python quantitative engine. It includes independent embedded CapitalBear and IQ Option browsers, nine user-configured asset slots per platform, reusable asset presets, normalized drag/resize calibration profiles, local engine health monitoring, and SQLite persistence.

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

Phase 4 has **not** started. There is no capture/parser pipeline, market extraction, signal generation, trading, or installer packaging.

## Workspace workflow

1. Open either or both platform workspaces. Log in manually inside each platform's browser.
2. Open **Asset Setup**, enter your own asset labels, enable the desired slots, and save. This does not change the platform's instruments; select those manually on the platform.
3. Create a named asset preset for reuse. Presets never cross platform boundaries.
4. Open **Calibrate Slots**. Drag the slot labels and resize with the lower-right handles. Create a profile, or save over the selected profile. Cancel discards unsaved geometry.

See [Platform sessions](docs/platform-session.md), [Calibration](docs/calibration.md), and [Asset presets](docs/asset-presets.md).

Contributions follow [Conventional Commits](CONTRIBUTING.md). The design and process boundaries are documented in [Architecture](docs/architecture.md).
