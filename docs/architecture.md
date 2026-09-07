# Architecture

## Current milestone

Phase 0 and Phase 1 establish a single cross-platform repository, the Electron application shell, two independently managed platform workspace windows, a shared contract package, and a local Python health/storage service. Later capture, calibration, quantitative analysis, and packaging phases extend these boundaries rather than bypassing them.

```text
Electron main process
├── Dashboard renderer
├── CapitalBear workspace window ── isolated persistent session partition
├── IQ Option workspace window ──── isolated persistent session partition
└── Loopback client/process manager
    └── Python FastAPI quant engine
        ├── HTTP /health
        ├── WebSocket /ws/health
        └── SQLite in the OS application-data directory
```

## Repository boundaries

- `apps/desktop` owns Electron lifecycle, secure preload APIs, React views, platform windows, and engine process supervision.
- `packages/shared-types` owns TypeScript message and state schemas shared by desktop processes. Arbitrary renderer or page payloads are not trusted.
- `services/quant-engine` owns the local API, heartbeat stream, migrations, and quantitative/storage code. Its Python dependencies remain separate from npm.
- Root scripts coordinate checks without introducing a task runner or another package manager.

## Desktop security boundary

The Electron renderer has no direct Node.js access. Browser windows use context isolation, disable Node integration, and expose only explicit validated preload methods. CapitalBear and IQ Option use distinct persistent session partitions so cookies, local storage, crashes, refreshes, and login state are not shared.

The user authenticates manually within the embedded platform. The application must never ask for or log a platform password. Platform pages cannot submit arbitrary commands to the Python service or receive raw Node.js capabilities.

## Local service boundary

The Python service listens on loopback by default. HTTP provides a simple startup/readiness probe, while WebSocket supports ongoing health and later state streaming without polling every feature update. Desktop behavior remains usable when the engine is absent: it reports an offline/degraded state rather than crashing.

SQLite is initialized in WAL mode under the dynamically resolved operating-system application-data directory. No database, browser profile, log, screenshot, or local dataset belongs in the repository.

## Window isolation

The Electron main process owns a separate `BrowserWindow` reference for each platform. Opening an existing workspace focuses it; closing one releases only that reference and must not terminate the dashboard, the other workspace, or the engine. Each workspace always models nine slots, rendered as a responsive 3x3 grid at normal desktop sizes.

## Execution safety

The architecture supports analysis, paper simulation, and explicit manual confirmation. Unattended real-money order submission is outside the allowed boundary. Future broker support must sit behind a capability-reporting adapter and keep live submission disabled or human-confirmed.

See [ADR 0001](adr/0001-lightweight-monorepo-and-local-health-transport.md) for the repository and local transport decision.

