# QuantScreen Trader

QuantScreen Trader is a cross-platform Electron desktop application with a local Python quantitative engine. It includes independent embedded CapitalBear and IQ Option browsers, nine user-configured asset slots per platform, reusable asset presets, normalized drag/resize calibration profiles, local engine health monitoring, and SQLite persistence. It now includes calibrated DOM/visual observation, local OCR, provenance and quality gates, a Python one-second and multi-timeframe market-data builder with Parquet storage, deterministic quantitative features, a regime-aware strategy ensemble that produces explainable analytical opinions, per-platform cross-asset ranking of those opinions, and a deterministic replay that re-runs that whole pipeline over recorded history for research. Replay and backtest results are research only and are never applied automatically to live decisions.

It also includes an execution layer that can press the broker's own entry controls automatically. That layer is off by default and must be armed deliberately; when armed in AUTO mode it places real-money orders without confirming each one. The application still never collects platform passwords or bypasses platform security controls. Read [Execution](docs/execution.md) before arming anything.

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

Phase 4 ✅ Authenticated nine-slot capture verified live on both platforms.

Phase 5 ✅ Deterministic market data builder.

Phase 6 ✅ Quantitative feature engine.

Phase 7 ✅ Regime-aware strategy ensemble. Analysis only: it forms directional opinions and abstains freely, and places no orders.

Phase 8 ✅ Cross-asset opportunity ranking. Ranking only: it orders the Phase 7 opinions the application is currently holding, one board per platform, and names a top analysis or refuses to.

Phase 9 ✅ Deterministic paper outcome simulation. Measurement only: it takes each finished Phase 8 selection, finds the canonical market price after the decision actually became available, waits a fixed horizon in market time, and records WIN / LOSS / DRAW / INVALID. It presses nothing, and it is not the desktop execution layer's PAPER mode.

Phase 9.5 ✅ Daily session guard. A stop system only: it keeps one trading day's realized profit and loss from the Phase 9 settlements, and withdraws permission for new entries when a profit target or loss limit is reached, when the operator ends the day, or when the accounting cannot be trusted. It changes no score, no threshold and no stake.

Phase 10 ✅ Outcome analytics and score calibration. Measurement only: it reads the resolved Phase 9 outcomes and reports whether the Phase 7 and Phase 8 scores actually corresponded to better results. It changes no formula, no threshold, no session limit and no execution setting, and there is no way in it to apply anything it finds.

Phase 11 ✅ Deterministic historical replay, backtest and walk-forward validation. Research only: it drives the *existing* Phase 5-10 pipeline over the recorded observation history in chronological order and reports what the frozen contracts would have decided and what those decisions would have produced. It changes no formula, no threshold, no session limit and no execution setting, writes only under its own namespace, and provides no execution-application mechanism. Phase 12 reads its evidence through a separate, past-only analytical admission boundary.

Phase 12 ✅ Evidence-gated adaptive analytical policy (`qst-policy-v1`) and watchdog (`qst-watchdog-v1`). Local validation and all required CI checks passed. Default mode **SHADOW** preserves baseline behavior. Optional PAPER_GATED filters paper intents only. No broker execution automation is implemented by Phase 12. See [Adaptive policy](docs/adaptive-policy.md).

Phase 13 ❌ NOT STARTED — Packaging.

Phase 14 ❌ NOT STARTED — Shadow-live operational validation.

Execution layer ⚠️ Implemented, not yet accepted against a live broker. It locates the broker's own direction controls in each of the nine cells, and in AUTO mode presses the one a finished board named. It sets no stake, no expiry and no account, reads no balance, and records no outcome. Verified by unit tests only — the control locator has never been run against a real broker panel, so measure the controls and watch a PAPER run before arming AUTO. Position sizing, outcome tracking and installer packaging remain unimplemented.

## Workspace workflow

1. Open either or both platform workspaces. Log in manually inside each platform's browser.
2. Open **Asset Setup**, enter your own asset labels, enable the desired slots, and save. This does not change the platform's instruments; select those manually on the platform.
3. Create a named asset preset for reuse. Presets never cross platform boundaries.
4. Open **Calibrate Slots**. Drag the slot labels and resize with the lower-right handles. Create a profile, or save over the selected profile. Cancel discards unsaved geometry.

See [Platform sessions](docs/platform-session.md), [Calibration](docs/calibration.md), and [Asset presets](docs/asset-presets.md).

Contributions follow [Conventional Commits](CONTRIBUTING.md). The design and process boundaries are documented in [Architecture](docs/architecture.md).


## Market data foundation

Load an asset configuration and calibration profile, then choose **Start observation** in a workspace. Disabled slots stay idle. Observation status, source, quality, age and series progress appear on each slot. Sampling targets are configurable per workspace; developer diagnostics expose timings, ROI and queue drops. Images are never stored.

Phase 4 ✅ Acceptance ran against both real authenticated sessions on 2026-09-09: nine opened tabs detected in physical order, the 3×3 canvas grid resolved from the actual screenshot, and each tab's own chart read live. Ambiguous chart text still produces DATA_UNCERTAIN rather than a fabricated price.

Phase 5 ✅ Deterministic samples, one-second selection, S5/M1/M5/M10 OHLC, forming/closed state, gaps, bounded buffers and Parquet/replay tests.

Phase 6 ✅ Deterministic, no-lookahead quantitative features over the canonical series: price action, trend, momentum, volatility, structure, noise, one-second micro state and per-timeframe snapshots with explicit readiness. Closed candles only; nothing repaints. Phase 6 produces facts, not signals — direction and strategy belong to Phase 7, and cross-asset ranking to a later phase that has not started.

Phase 7 ✅ Deterministic regime classification and a six-strategy ensemble over the `qfe-v2` bundles: a primary regime with all of its supporting scores, one explainable evaluation per strategy with its reasons and vetoes, and a weighted vote that abstains rather than forcing a direction. Evidence is normalized by each series' own volatility, so one set of documented thresholds describes a five-second bar and a ten-minute bar alike. `confidence` measures how much usable, agreeing evidence exists — it is not a win probability, and Phase 7 has no calibration behind it. There is no order, stake, payout, bankroll or broker control anywhere in the layer, and tests assert that the package binds no execution name and imports nothing that could reach a broker.

Phase 8 ✅ Per-platform cross-asset ranking over the `qst-strategy-v1` ensembles: one board per platform for one primary close, ranking only the snapshots that describe that same market decision time. It consumes finished Phase 7 ensembles and nothing else — no broker price, no raw feature, no recomputed indicator, no copy of a strategy. `rankScore` orders the opportunities that currently exist; it is not a win probability, an expected return or a payout-adjusted value, and no outcome has ever been observed by this system to calibrate one. A board names a top analysis only when the cohort is closed, the leader clears a minimum score on its own, and it stands clear of the runner-up — otherwise it reports NO_OPPORTUNITY rather than forcing a winner. CapitalBear ranks on its S5 close and IQ Option on its M1 close, and the two boards are never merged into one 1–18 list. There is no order, stake, payout, bankroll or broker control anywhere in the layer, and tests assert that the package binds no execution name and imports nothing that could reach a broker.

Phase 9 ✅ Deterministic paper outcome simulation over the `qst-ranking-v1` boards: one hypothetical entry per READY selection, priced at the first canonical sample at or after the moment the complete decision became available — never at the board's close time, which is earlier than the cohort that describes it could possibly have been assembled. It holds for five seconds on CapitalBear and sixty on IQ Option, resolves on the first canonical price at or after that horizon, and reports WIN / LOSS / DRAW, or INVALID when the data it needed never arrived. There is no lookahead anywhere: no pre-signal price, no candle high or low, no better price chosen later. Simulated money is reported only when an operator has configured an explicit stake, payout rate and currency, and is `None` — never zero — otherwise; nothing reads a broker wallet or payout. The statistics are descriptive and calibrate nothing: Phase 9 records evidence and Phase 10 will analyse it. There is no order, press, broker control or execution import anywhere in the layer, and tests assert that the package binds no execution name and imports nothing that could reach a broker. See [Paper simulation](docs/paper-simulation.md).

Phase 9 acceptance ran against both real authenticated sessions on 2026-09-11, read-only and with the execution layer untouched. The paper layer was live inside the real pipeline for both runs, tracked canonical market time from Phase 5 events, and produced **no paper trade at all**: across 204 Phase 7 ensembles and 65 finalized boards, CapitalBear's cohorts stayed COLLECTING on immature, partially captured input and IQ Option's completed as NO_OPPORTUNITY with every candidate still WARMING and SKIP, so Phase 8 never named a selection. That is the correct outcome and the intended one — Phase 8's gates were not lowered, PARTIAL boards were not admitted, and no trade was injected to make the run look productive. The engine logged no error over either run. Phase 9's behaviour is proved by its unit, replay and end-to-end pipeline tests, which drive the real Phase 5-8 chain from broker-shaped observations and resolve real outcomes from it.

Phase 9.5 ✅ Deterministic daily session accounting over the `qst-paper-v1` settlements: one session per local trading date in the operator's own timezone, a realized-only running total, and a hard `canOpenNewEntry` permission the execution layer reads as one more named refusal. A profit target or loss limit stops the day the moment realized money crosses it, and never un-stops when a trade that was already running settles the other way. Money is counted once per `tradeId` — the processed ids live in the persisted event log, so a restart cannot double a day's profit — and only when Phase 9 actually measured some, in the session's own currency; there is no automatic currency conversion and no broker balance is read anywhere. A locked day comes back locked after a restart and is not unlocked by switching the guard off. The guard changes nothing upstream: it has no stake, no direction, no score and no martingale, and tests assert the package binds none of those names and imports no feature, regime, strategy or ranking module. Python reports `shutdownRequested`; Electron owns the application's lifetime. See [Daily session guard](docs/session-guard.md).

Phase 10 ✅ Outcome analytics and score calibration over the `qst-paper-v1` outcomes: resolved paper trades only, collapsed to their terminal state, version-checked against all five upstream contracts, and sorted by market time so the same stored history always produces the same snapshot id. It reports data quality before any performance claim — a win rate over the fifth of decisions that happened to resolve is not a win rate — then empirical outcome curves for `rankScore` and `ensembleConfidence`, Wilson intervals on every rate, Spearman correlations with draws excluded and counted, and segmentation by platform, asset, regime, regime × direction, hour, weekday, agreement, lead margin and input quality. `rankScore` and `ensembleConfidence` were produced by layers that had never observed an outcome, so a bin's win rate is an *empirical outcome curve* and never a probability calibration; there is deliberately no Brier score anywhere in the package. Threshold research is searched on a chronological TRAIN split only and evaluated out of sample on VALIDATION and TEST, with coverage and sample floors in the objective and `UNSTABLE` marked rather than recommended; the split is never shuffled, and no objective in the layer knows about a daily profit target or loss limit. A score that does *not* work is reported as loudly as one that does, including when it is inverted. Nothing is applied: there is no `POST` on the API, no button in the panel, and no code in the application that reads a discovered threshold. Tests assert the package binds no execution, session-guard or stake name and imports no feature, regime, strategy, ranking or paper engine. See [Outcome analytics](docs/analytics-calibration.md).

Phase 10 acceptance ran against this installation's real history on 2026-09-12, read-only. It found 12,744 stored Phase 7 evaluations and **no paper trades at all**, which matches Phase 9's own acceptance: Phase 8 never named a selection during those runs, so nothing was ever measured. The analysis reported `totalResolved: 0` with `INSUFFICIENT_SAMPLE`, `PAPER_ACCOUNTING_UNAVAILABLE` and `NO_STRATEGY_EVIDENCE`, and produced no win rate, no correlation and no threshold candidate. That is the correct outcome and the intended one — no history was fabricated to make the run look productive. Phase 10's behaviour is proved by its unit, determinism, isolation and API tests, and by a deterministic synthetic corpus in CI that carries a genuinely stronger score band and a deliberately uninformative one, so the layer has to find the first and report the second.

Phase 11 ✅ Deterministic historical replay over the durable Phase 4 observation record: canonical ordering by market time and identity (never by file, row group or modification time), provenance relabelled REPLAY so a recorded reading cannot masquerade as a live one, and a market clock that advances only from recorded events. It contains no indicator, regime rule, strategy, rank score or outcome rule of its own — it constructs an isolated `MarketEngine`, the same object the live process runs, and a test drives one deterministic record through both the live HTTP entry point and the replay driver and requires identical Phase 6, 7, 8 and 9 outputs from each. Warm-up is derived from `qfe-v2` rather than guessed (fifty bars of the slowest timeframe each platform reads: 4h10m for CapitalBear, 8h20m for IQ Option), warm-up decisions are refused at creation so no warm-up trade exists to filter out, and a settlement tail derived from `qst-paper-v1` lets a selection made inside the window finish outside it without allowing a new one. A causality audit asserts, on every persisted record, that no feature, ensemble, board or entry ever saw past the clock. Walk-forward folds are strictly chronological with purge and embargo sized from the longest possible trade lifetime, candidates are discovered on TRAIN only and merely evaluated afterwards, and `NO_STABLE_CANDIDATE` is a valid answer that no sample floor is lowered to avoid. Latency and payout studies are labelled SIMULATION ONLY and tune nothing. Nothing is applied: there is no apply endpoint, no button in the panel, and a test searches the whole repository and asserts no code reads `ReplayEvidence`. See [Replay and backtest](docs/replay-backtest.md).

Phase 11 acceptance ran against this installation's real history on 2026-09-12, read-only, over a copy so the live record could be proved untouched. The record holds 21,239 canonical observations across 23 assets and 204 contexts between 2026-09-09 14:14 and 2026-09-11 15:08 UTC — three trading dates, five hours of day, and a 40% gap share. Replaying all of it drove 3,653 Phase 7 ensembles and 600 finalized Phase 8 boards and produced **no selection and no paper trade at all**, with `featuresReadyAt` never reached: the longest unbroken context in the record runs about ten minutes, and `qfe-v2` needs fifty closed primary bars plus mature higher-timeframe context before anything may claim READY. That reproduces Phase 9's and Phase 10's own acceptance findings exactly, and it is the correct outcome — no history was fabricated and no gate was lowered to make the run look productive. The replay reported `INSUFFICIENT_HISTORY`, `LOW_SELECTION_COUNT`, `NARROW_TIME_COVERAGE`, `HIGH_GAP_RATE`, `INSUFFICIENT_FOLDS` and `NO_STABLE_CANDIDATE`, ran the same manifest twice to a byte-identical result and run id, and left every pre-existing file in the record byte-identical. **No performance claim is made from this dataset.** Phase 11's behaviour is proved instead by its equivalence, causality, determinism, walk-forward, isolation and API tests, and by a deterministic synthetic fixture — clearly separated, and carrying `SYNTHETIC_BEHAVIOR_TEST` on every result. Replay performance was measured rather than asserted: 100,000 events at 4,080/second and 1,000,000 at 3,196/second, both with zero causality violations and bounded memory (see [Development](docs/development.md#phase-11-replay-benchmark)).

Phase 11.1 closed four correctness gaps found by auditing that work, and Phase 11.2 moved the replay contract to `qst-replay-v2` to close it out: two of those fixes change the numbers a replay reports from unchanged history, and the run id is a UUID5 keyed on the version string, so holding it still would have let a new result overwrite an old one at the same path with nothing on either record saying they were produced differently. The one that mattered: Phase 5 keys a series partly on which capture path produced it, so a DOM-to-OCR fallback ends one series and starts another — and relabelling every replayed reading `REPLAY` erased that transition, letting a replayed series run straight through a reset the live run really performed. Provenance and identity are now separate ideas: the reading still carries `REPLAY` so it can never masquerade as live, while the identity keeps the source that was recorded, and a regression test drives a record whose capture path changes hands twice through both paths and requires identical candles, features, ensembles, boards and outcomes. Alongside it: walk-forward now purges on `decisionAvailableAt` rather than `boardAsOf`, because a board is assembled after the bar it describes closed and the old boundary discarded selections that genuinely became actionable inside the window; cancellation is sticky for the whole job, so a run stopped after its baseline can no longer report COMPLETED; and a Parquet file that will not open is isolated and counted instead of taking the readable history down with it.

Phase 8 acceptance ran against both real authenticated sessions on 2026-09-10, read-only. Every new Phase 7 ensemble produced exactly one Phase 8 candidate — 882 ingested, 0 duplicated, 0 out of order — and each platform ranked its own same-time cohort on its own primary close. CapitalBear's capture rate does not close all nine S5 bars inside every five-second window, so most boards finalize as PARTIAL with the missing slots named rather than invented. Directional candidates were ranked live, in both directions, and none cleared the selection gate: every one stayed WATCH on immature, DEGRADED input, so no board named a top analysis. That is the intended outcome, not a failure — the ranking pipeline reports what it has instead of forcing a winner.

See [Providers](docs/market-data-providers.md), [Capture and parsing](docs/capture-parser.md), [Market data builder](docs/market-data-builder.md), [Quant feature engine](docs/quant-feature-engine.md), [Strategy engine](docs/strategy-engine.md), [Opportunity ranking](docs/opportunity-ranking.md), [Paper simulation](docs/paper-simulation.md), [Daily session guard](docs/session-guard.md), [Outcome analytics](docs/analytics-calibration.md), [Replay and backtest](docs/replay-backtest.md), [Execution](docs/execution.md) and [Data quality](docs/data-quality.md). After updating, install the Python dependencies again to obtain DuckDB: `node scripts/python.mjs -m pip install -e "services/quant-engine[dev]"`.


The interrupted temporary-profile acceptance check is complete, including both unauthenticated workspaces, idle disabled slots, calibration pause and 18-stream synthetic HTTP/Parquet validation. Real authenticated broker extraction has since been verified on both platforms. See the [2026-09-08 verification results](docs/development.md#acceptance-continuation--2026-09-08).


## Asset synchronization

Workspaces now offer **Sync Assets** and **Auto Sync Assets**, with AUTO/MANUAL locks in Asset Setup. Confident, correctly mapped detections populate and enable AUTO slots; uncertain reads preserve existing values. Auto changes create per-slot series boundaries. See [Asset detection](docs/asset-detection.md) for mapping, confidence and debounce rules.

The inspected live broker pages render their trading UI into a single canvas. Their current default full-browser grid must be aligned with chart regions before safe OCR synchronization; authenticated asset/price extraction is still pending. DOM fixture success does not establish support for these canvas layouts.
