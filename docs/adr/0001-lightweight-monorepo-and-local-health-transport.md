# ADR 0001: Lightweight monorepo and local health transport

- Status: Accepted
- Date: 2026-09-07

## Context

The application combines Electron/TypeScript packages with a Python 3.12 service and must run from one repository on macOS ARM64 and Windows x86-64. The desktop needs a cheap readiness check during startup and a streaming channel for ongoing engine state.

## Decision

Use native npm workspaces for `apps/*` and `packages/*`, with root npm scripts coordinating the separately packaged Python service. Do not add a monorepo task-runner dependency until measured build complexity requires one.

Run the quant engine as a loopback-only FastAPI process. Use `GET /health` for one-shot readiness and `WebSocket /ws/health` for continuous heartbeat/state delivery. Keep message schemas explicit at process boundaries.

## Consequences

- A new machine needs Node/npm and Python, but setup remains transparent and platform-neutral.
- npm and Python retain their native lock/package metadata instead of being hidden behind custom tooling.
- The desktop can degrade gracefully when the engine is unavailable and reconnect to the streaming endpoint later.
- Loopback transport is observable and testable, but callers must validate messages and avoid exposing the service on external interfaces.

