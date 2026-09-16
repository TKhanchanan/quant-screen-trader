# Phase 14 — shadow-live operational validation

**Implementation and live acceptance are separate. Phase 14 remains in progress until
both brokers have sustained macOS arm64 evidence, a controlled restart, and green CI.**
Phase 12 remains closed; Phase 13 macOS is accepted and Windows is deferred.

The opt-in `qst-shadow-live-v1` recorder observes the existing Phase 4–12 pipeline.
It does not change policy mode, features, strategies, gates, paper semantics, accounting,
watchdog thresholds, execution settings, or broker controls. No screenshots, raw OCR text,
cookies, browser storage, credentials, broker balances, or exception/path text enter its output.
Existing diagnostics remain the UI. All P/L in the report is **paper accounting**.

## Start on macOS arm64

After local lint, typecheck, tests and build pass, launch the development app from the
repository with the existing external data root and manually maintained browser sessions:

```sh
export QST_SHADOW_LIVE=1
export QST_COMMIT_SHA="$(git rev-parse HEAD)"
unset QST_SHADOW_LIVE_RUN_ID
npm run dev
```

Do not set a different `QST_DATA_DIR` when checking persistence of the operator's existing
session. The packaged app can also inherit these environment variables when its binary is
launched from a shell, but it must contain the tested Phase 14 code and bundled engine.
The old Phase 13 binary cannot record Phase 14.

1. Manually open CapitalBear and IQ Option, log in if necessary, and show real market charts.
2. Verify **execution is DISARMED / OFF**, including both workspaces. Never arm, test order
   controls, or press a broker entry control during acceptance.
3. Confirm `/api/policy/state` reports **SHADOW**. Existing policy history is respected;
   the recorder never activates a snapshot or switches PAPER_GATED back to SHADOW for you.
4. Use existing calibration, asset synchronization and Start observation controls. Keep
   both surfaces available. Record a deliberate asset/context transition using normal
   operator controls. Do not change analytical thresholds if boards remain incomplete.
5. Accumulate at least **24 hours of qualified capture in total**, with at least **23 hours per broker**. Simultaneous broker intervals count once toward the total; process uptime and restart downtime do not count.
   CapitalBear primary is S5; IQ Option primary is M1. M5/M10 stay context only.

## Evidence and endpoints

The engine writes only `<app-data>/phase14/<runId>/`:

- `summary.json`: atomic checkpoint, run counters, per-platform/per-slot state and latency.
- `events.jsonl`: board, selection, policy, paper, context/source/reset and health events.
- `lease.sqlite3`: process-held exclusive lease; prevents simultaneous writers to one run.

Use the configured loopback port (8765 by default). These endpoints reject browser origins
and require the same local-user trust boundary as existing engine APIs:

```sh
curl --fail http://127.0.0.1:8765/api/shadow-live/state
curl --fail -X POST http://127.0.0.1:8765/api/shadow-live/checkpoint
```

Desktop telemetry is automatic while opted in. It sends only a closed, bounded schema to
`POST /api/shadow-live/telemetry`; there is no command forwarding. Quant records observations
at ingestion rather than attempting to reconstruct acceptance from UI polling. Desktop OCR
exceptions, which produce no observation, remain distinct from engine-rejected observations.

Board counts count distinct board revisions, including immutable PARTIAL finalizations.
Policy counts deduplicate decision IDs; paper counts count distinct lifecycle transitions.
READY events include the selected candidate, confidence, lead margin, agreement and regime.
Policy events preserve their original baseline/adaptive actions, evidence status, snapshot,
matched rules, vetoes and reasons. They are never re-evaluated against newer evidence.

Latency is in milliseconds. `captureToAccepted` measures wall time from capture to canonical
acceptance. The four downstream spans use a monotonic clock at actual processing boundaries:
`primaryAvailableToEnsemble`, `ensembleToBoard`, `boardToPolicy`, `policyToPaperIntent`.
`count` and `max` cover the run; p50/p95 are nearest-rank percentiles over the **last 2048
samples in this process**, with the window count explicitly reported. Capture intervals and
main-loop delay have their own metrics. Renderer stalls are not directly measured.

Causal validation tracks the parsed availability of observations **inside each candle's
window**, then propagates required availability through feature bundles and board candidates.
An arrival that closes a prior candle does not lend its later price to that candle.
The recorder checks joined context timestamps, decision availability, policy evidence time,
context identity, entry/expiry timing, canonical price equality and first eligible sample.
Any detected violation is a hard failure; instrumentation never repairs or retimes a decision.

Arrays and identity caches are bounded: 512 pending events, 64 recent events/segments,
4096 deduplication/lineage entries per cache, and 2048 samples per fixed latency stream.
Detailed events stop at 64 MiB per run; overflow is explicit and prevents acceptance.
No historical market records are compacted or rewritten. Checkpoints replace one file.
Instrumentation errors are counted and fail acceptance without suppressing baseline ingestion.

## Controlled restart and storage checks

Checkpoint and note `runId`. Quit the **application normally**, verify its engine exits,
and relaunch the same build and data root with that UUID:

```sh
export QST_SHADOW_LIVE_RUN_ID='<runId from the checkpoint>'
npm run dev
```

Each process segment is distinct; continuation preserves counters and excludes downtime.
Without that variable the next launch gets a new UUID. Concurrent ownership is refused;
a different commit/application version cannot continue an old run. An unclean restart stays
visible and prevents acceptance. Four engine starts inside 60 seconds flag a crash loop.
A restart counter alone does not verify browser or accounting persistence.

Observe browser login persistence, configuration, calibration, asset presets, safe paper
cancellation/restoration, the current session guard (a locked guard must stay locked), policy
journal persistence, and absence of an orphan engine. Then submit those actual observations to
`POST /api/shadow-live/verify-restart` using this schema, with each value reflecting reality:

```json
{
  "browserSessionPersisted": true,
  "configurationPersisted": true,
  "calibrationPersisted": true,
  "assetPresetsPersisted": true,
  "paperRestoredSafely": true,
  "sessionGuardRestoredWithoutUnlock": true,
  "policyJournalRestored": true,
  "noOrphanEngine": true,
  "executionStayedDisarmed": true,
  "noBrokerPresses": true
}
```

This is labeled **OPERATOR_OBSERVATION**, not an automated proof. Do not submit it before
performing the checks. Resume capture after restart and record actual durations.

After stopping capture, explicitly audit storage:

```sh
curl --fail -X POST http://127.0.0.1:8765/api/shadow-live/verify-storage
```

This flushes the existing buffer, runs SQLite quick checks on configuration and policy stores,
and reads/revalidates Parquet files written since run start, one file and 256 rows at a time.
It reports file/row/byte counts. The audit holds the existing busy gate; do it after stopping
capture. The scan caps at 10,000 new Parquet files and remains incomplete beyond that cap.
Check file counts and duplicate/drop counters for operational growth; the recorder does not
invent an expected file-growth rate or rewrite storage. A read error remains unverified;
confirmed SQLite corruption or repeated storage failure fails the run.

## Acceptance and export

`health` is HEALTHY / DEGRADED / FAILED. `acceptance` is PENDING / COMPLETE / FAIL, separate
from `result` PASS / DEGRADED / FAIL. A short healthy smoke stays **PENDING**. Unknown safety
or storage checks are `null`/unverified, not fabricated zeros. NO_OPPORTUNITY and zero paper
trades do not fail health; normal uncertainty/rejections can produce DEGRADED and still be
valid evidence once all hard checks and duration requirements are satisfied.

Capture duration accrues only between recent desktop heartbeats while capture, engine and
surface are available and enabled slots have recent completed capture/OCR attempts. A
pre-capture identity failure, including `TAB: identity is uncertain` / **Sync Assets required**,
does not increment observation or data-uncertainty counters, refresh the attempt clock, or count
toward live duration. Grid, calibration and unavailable-surface failures likewise remain outside
live capture time. Failed OCR after a real capture attempt counts as observation work without
inventing market samples.
Telemetry must report engine availability and eligible synchronized slots; receipt of a heartbeat alone is insufficient. Gaps longer than five seconds break continuity. `qualifiedCaptureDurationMs` is the union of credited platform intervals; each platform also reports `captureDurationMs`. Idle process lifetime never satisfies the 24-hour / 23-hour targets.

The closed telemetry schema includes per-slot attempts, parsed/GOOD/UNCERTAIN counts, latest attempt/parse/GOOD times, and 1s/S5/M1 samples. Operational snapshots enter the bounded recorder every five minutes, including main-process RSS and Auto Sync counters. Keep Auto Sync off for the first 30–60 minutes. Applied automatic changes require review against the actual visible instruments; they are never automatically declared expected. The market queue is bounded at 180 observations, transmitted in batches of at most 18. Crossing the batch size is not unbounded growth; exceeding the queue capacity is a hard failure. Detailed event overflow is also a hard failure.
Broker-press and armed observations are sticky failure signals. The execution observer can
miss activity during telemetry gaps, so independent operator verification is required.

Export `/api/shadow-live/checkpoint` to `docs/evidence/phase14/shadow-live-acceptance.json`
after verifying its contents. Include normal CI reference and the exact tested commit in the
acceptance notes. Never commit the browser profile, screenshots, secrets, or a whole data root.
The initial committed artifact explicitly records **LIVE ACCEPTANCE PENDING**; synthetic test
fixtures are never substituted for market evidence. Do not mark Phase 14 closed until the
actual sustained session, restart, protected diff review, and normal GitHub CI all pass.

## Auto Sync review during the soak

The recorder counts Auto Sync scans and applied slot changes separately from manual Sync. When automatic changes occur, inspect the visible instruments and recorded context transitions before submitting `POST /api/shadow-live/verify-auto-sync` with `platform`, `reviewedAppliedChanges` (the current recorded count), and `unexpectedAutoSyncChanges` (the actually observed number). The attestation is labeled `OPERATOR_OBSERVATION`. An unreviewed applied change keeps acceptance pending, a later change invalidates the earlier review, and any unexpected change fails acceptance. Never attest zero without checking.
