# Architecture

## Current milestone

Phases 0–3 retain the Electron/React/shared-types and FastAPI/SQLite boundaries. Workspace windows contain a main-process-owned remote WebContentsView. Calibration uses a separate transparent local WebContentsView above it, not injected code in a platform page. The workspace renderer retains the toolbar and configuration UI.

```text
Electron main process
├── Dashboard renderer
├── CapitalBear workspace window → remote view / isolated persistent partition
│   └── temporary local transparent calibration overlay
├── IQ Option workspace window → remote view / isolated persistent partition
│   └── temporary local transparent calibration overlay
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

Only local renderer webContents IDs are registered for IPC, and only their main frames are authorized. Workspace operations require the sender's platform to match. Overlay renderers may read browser state and update the in-memory calibration draft, but cannot save configuration or reload a platform. Remote browser views have no application preload. Popup windows, downloads and permission requests are denied; main-frame navigation is limited to exact configured HTTPS origins.

## Local service boundary

The Python service listens on loopback by default. HTTP provides a simple startup/readiness probe, while WebSocket supports ongoing health and later state streaming without polling every feature update. Desktop behavior remains usable when the engine is absent: it reports an offline/degraded state rather than crashing.

SQLite is initialized in WAL mode under the dynamically resolved operating-system application-data directory. No database, browser profile, log, screenshot, or local dataset belongs in the repository.

`POST /api/workspaces/{platform}/configuration` accepts a discriminated operation contract: `get`, `slots`, `savePreset`, `loadPreset`, `deletePreset`, `saveCalibration`, `loadCalibration`, or `deleteCalibration`. Rename uses save with an existing ID; duplication uses save without an ID. The response contains current slot configuration, platform presets/profiles, and the active calibration ID. Zod validates both IPC input and engine responses; Pydantic validates HTTP input. Engine error bodies and platform URLs are not forwarded to the renderer.

The HTTP router delegates to validated domain models and a parameterized SQLite repository. Changes and their resulting snapshot use one transaction. Browser-origin requests are rejected and no CORS access is enabled. This is a local-user trust boundary, not protection against malicious native software running as the same OS user.

Migration `0002_calibration_and_presets.sql` adds `calibration_profiles`, `calibration_slots`, `asset_presets`, `asset_preset_slots`, and `active_calibrations`. Migration 0001 is unchanged. Existing `workspace_profiles`/`slot_profiles` remain the source for current asset assignments; the first existing workspace for each platform is reused. Nine missing slots are initialized disabled. Geometry exists only in calibration profiles, not duplicated into asset presets. Profile child rows cascade on deletion; deleting an active profile clears its active reference.

## Window isolation

The Electron main process owns a separate `BrowserWindow` reference for each platform. Opening an existing workspace focuses it; closing one disposes its remote view and overlay without touching the other workspace or engine. Reopening reuses the platform's persistent partition. A remote renderer crash becomes an ERROR state; Reload recovers that view without an automatic reload loop. Each workspace always models nine logical slots.

Zustand owns renderer session/configuration snapshots and request status. React components own unsaved form drafts; Electron owns only the temporary overlay draft and native view geometry. Durable data belongs to the engine. Renderer ResizeObserver measurements use browser-region coordinates in device-independent pixels; persisted slot bounds remain normalized and scale with that region.

## Execution safety

The architecture supports analysis, paper simulation, and — since the execution layer — unattended submission that the operator explicitly arms. AUTO mode presses the broker's own direction control with real money and no per-order confirmation. This is a deliberate change from the earlier boundary, made by the project owner, and the safeguards are structural rather than a prompt before each order: an off-by-default mode, a separate arm step, a stop control nothing can refuse, per-surface control maps that expire when the page moves, a cooldown, an hourly cap, and a breaker that disarms after presses the broker never visibly answered.

The layer never sets stake, expiry or account. It presses one direction control in one cell and inherits whatever the broker panel already has selected.

`PlatformBrowserManager.pressPoint` is the single place that produces input for a broker page; nothing else in the application may send input events. The Python engine remains analysis-only and cannot trigger a press. See [Execution](execution.md).

"Paper" names two unrelated things and they are never merged. The desktop execution layer's PAPER mode decides whether it *would* have pressed a broker control and does not press it; a ticket in state CONFIRMED means the broker panel visibly reacted. The Phase 9 `PaperEngine` lives in the Python engine, never sees a broker control at all, and measures what the market did after a Phase 8 selection; a trade in outcome WIN means the market moved the way the analysis said. Phase 9 imports no execution module, binds no execution name, and places nothing — the simulated stake and payout rate it can report are explicit operator parameters, not broker state. See [Paper simulation](paper-simulation.md).

See [ADR 0001](adr/0001-lightweight-monorepo-and-local-health-transport.md) for the repository and local transport decision.


## Phase 4–5 data flow

Main-process providers observe calibrated remote-view slots → strict shared observation contract → bounded latest-per-slot HTTP batches → Python quality gate → canonical samples → one-second stream and aligned S5/M1/M5/M10 candles → buffered Parquet. DuckDB provides serialization and research queries. No OHLC logic is duplicated in TypeScript and no SQLite migrations changed.

Only trusted workspace main frames can issue observation start/stop/state IPC. Calibration overlays and remote pages cannot. Configuration responses reset provider context, and browser lifecycle/geometry changes invalidate in-flight capture. Existing Google navigation and partition ownership are preserved.

See [provider boundaries](market-data-providers.md), [scheduler and capture](capture-parser.md), [builder/storage semantics](market-data-builder.md), and [quality gates](data-quality.md). Live chart extraction remains subject to broker-specific verification; Phase 4 is not yet signed off for authenticated data accuracy.
