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
| `QST_DATA_DIR` | OS application-data directory | Optional standalone-engine data directory. |
| `QST_PYTHON_EXECUTABLE` | Project virtual environment, then system Python | Optional interpreter override used by desktop/root scripts. |

Keep the host loopback-only. No credential belongs in `.env`; platform authentication occurs manually inside its isolated embedded-browser profile.

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

## Troubleshooting

- **Python is not found:** create `services/quant-engine/.venv` using the README commands, or set `QST_PYTHON_EXECUTABLE` to a Python 3.12+ executable.
- **Engine health is offline:** run `npm run engine:dev` and read the terminal error. Check that port 8765 is free and that the editable Python package is installed.
- **The database cannot initialize:** verify that the current user can write to the OS application-data directory. Do not move the database into the checkout.
- **`npm ci` rejects the lockfile:** use the Node/npm versions above. If dependencies intentionally changed, run `npm install`, validate, and commit both manifest and lockfile changes.
