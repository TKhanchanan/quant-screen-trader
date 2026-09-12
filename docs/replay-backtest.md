# Replay and backtest (`qst-replay-v1`)

Phase 11 answers one question:

> If the current frozen quant pipeline had been running over a historical market record, in
> chronological order, what would it actually have decided, and what would those decisions have
> produced?

And then a second, harder one:

> Are the relationships Phase 10 found still there when they are tested on periods that had not
> happened yet when they were found?

Everything below is research. No replay result is read by any live decision, and Phase 11 ships
no way to apply one — no endpoint, no button, no consumer.

## One brain

The replay package contains **no** indicator, regime rule, strategy, rank score or outcome rule.
It constructs an isolated `MarketEngine` — the same object the live process runs — hands it
historical observations in canonical market order, and records what came out:

```
durable Phase 4 observation record
        ↓  canonical ordering, provenance relabelled REPLAY
   ReplayClock (market time only)
        ↓
existing Phase 5   market_builder / market_models   ← acceptance, candles, quality, gaps
        ↓
existing qfe-v2     features/                       ← indicators, warm-up, identity reset
        ↓
existing qst-regime-v1 / qst-strategy-v1  strategy/ ← regime, strategies, ensemble
        ↓
existing qst-ranking-v1  opportunity/               ← cohort, board, selection
        ↓
existing qst-paper-v1    paper/                     ← entry, horizon, expiry, W/L/D
        ↓
resolved hypothetical outcomes
        ↓
existing qst-analytics-v1  analytics/               ← dataset, metrics, thresholds
        ↓
walk-forward evidence, robustness report, ReplayEvidence
```

A disagreement between live and replay on identical canonical events is a bug in one of them,
not a difference between two implementations. `tests/test_replay_equivalence.py` drives one
deterministic record through both the live HTTP entry point and the replay driver and requires
the same Phase 6, 7, 8 and 9 outputs from each, snapshot for snapshot.

The only thing the replay adds to the pipeline is `WindowedPaperEngine`, a `PaperEngine`
subclass that overrides exactly one method. It decides *whether a selection may become a paper
intent at all* — warm-up decisions may not, decisions after the evaluation window closes may
not, and a sandbox session-guard scenario can withdraw permission. Every timing, entry, expiry
and outcome rule is inherited unchanged, and a test asserts the override set is `{on_board}`.

## Where a replay begins

`REPLAY_ENTRY_LAYER = PHASE4_MARKET_OBSERVATION`.

The earliest durable canonical market representation this repository persists is the raw
`MarketObservation`: the provenance- and quality-carrying reading that Phase 5 either accepts
into the canonical series or rejects. Starting there means **Phase 5's own selection rules are
re-tested by the replay** — the parser-confidence floor, the quality gate, the parse-latency
bound and the one-sample-per-instant rule all run again — rather than being inherited from an
already-accepted sample.

Nothing is fabricated. A row that will not validate is counted as `malformed` and skipped; a
gap in the record stays a gap, with no interpolation, forward fill or manufactured candle; and
quality and degraded states travel through exactly as recorded.

### Provenance is relabelled; identity is not

Two labels, deliberately, because they answer two different questions.

`sourceType` becomes `REPLAY` (or `SYNTHETIC` for a constructed fixture), so a recorded reading
can never masquerade as something a broker surface produced just now.

`identitySourceType` keeps the source that was actually recorded, because **Phase 5 keys a series
partly on it**. The capture layer falls back from DOM to OCR mid-series on purpose, and that
fallback ends one series and starts another — the forming bars are dropped and the series begins
again. Collapsing both DOM and VISUAL into one replay label would erase that transition, and the
replayed series would run straight through a reset the live run really performed, inheriting
candle continuity, warm-up, regime context and ranking history it never had. The replay would
then be reporting what the pipeline *would* have done on a record the pipeline never saw.

| | `sourceType` | identity turns on |
| --- | --- | --- |
| Live | `DOM` / `VISUAL` | its own `sourceType` |
| Replay of a recorded run | `REPLAY` | the recorded `DOM` / `VISUAL` |
| Synthetic fixture | `SYNTHETIC` | `SYNTHETIC` |

`identitySourceType` is excluded from serialization, so the durable record is byte-for-byte what
it always was — this is an in-flight distinction, not a new column — and a validator refuses it
on any reading labelled `DOM` or `VISUAL`, so nothing arriving over the local ingest endpoint can
fake a series reset.

The quality block, the parser confidence, the latencies and the timestamps are untouched, because
the whole point of replaying real history is to put the real degraded states back through the
real gates.

`test_replay_equivalence.py` drives a record whose capture path changes hands twice — VISUAL, then
DOM, then VISUAL, on one platform, slot, asset, context and calibration profile — through the live
entry point under its recorded labels and through the replay driver under `REPLAY`, and requires
the same candles, features, ensembles, boards and outcomes from both. A second test proves the
comparison is not vacuous: the bar spanning each hand-over comes back with half a minute of a
minute, and a control run pinned to one source keeps the whole minute.

## Input fingerprint and run identity

`inputFingerprint` is a SHA-256 over **every semantic field of every accepted row**, in
canonical order, rendered from the validated model rather than from the stored bytes. A curated
field list is a list that drifts; rendering from the model means the same logical history
fingerprints identically whether it arrived from Parquet or from a fixture. No modification
time, file name or folder date takes part.

`replayRunId` is a UUID5 over `REPLAY_VERSION | inputFingerprint | settingsFingerprint |
evaluationStart | evaluationEnd | sorted platforms`. The same history under the same settings is
the same run; one changed event, or one changed setting, is a different one.

## The replay clock

`ReplayClock` advances only from historical market event timestamps and is monotone by
construction. `datetime.now` appears nowhere in the analytical half of the package, and a test
asserts that `clock.py`, `source.py`, `walk_forward.py`, `robustness.py`, `report.py` and
`models.py` import no `time` module and call no clock accessor. The one wall clock a run is
allowed is the stopwatch on the run metadata, which measures how long the backtest took and is
read by nothing that reaches a number. Varying the host clock produces a byte-identical result.

The clock also publishes a **watermark** — market time less `AVAILABILITY_LAG_MS` (3,000 ms) —
which is the same lag the live engine applies on its background maintenance pass. Knowing that
market time has reached *T* is knowledge available at *T*, so a quiet slot's bar closes at the
same point in the series it would have closed at live rather than whenever that slot next
happens to report. A test asserts the live literal still agrees with the replay constant.

## Event ordering

Canonical order is:

```
market event timestamp,
then platform, slotId, assetName, contextId, observation id
```

Nothing in the key is measured. `parsedAt` is deliberately absent: it records how long a parser
took, so ordering by it would impose a capture-latency order on events whose true causal order
is unknowable. File name, row-group position and modification time are absent for the same
reason — `ParquetStorage` writes uuid-named files per partition and reads them back in whatever
order the filesystem offers, so the durable record is an **explicitly unordered set** and
canonical sorting is the contract rather than a repair. The `outOfOrder` diagnostic counts how
many rows sat later in storage than a row describing an earlier instant; on this installation's
real record it is 133, which is the evidence that the sort was needed.

Same-instant events are never given knowledge of each other. Each slot owns its own series
builder, so a row can only reach the slot it belongs to; a second row at the same instant on the
same slot is refused by Phase 5 rather than ordered; and the one thing shared across slots — the
availability watermark — is a function of the instant itself, so every ordering of one instant's
rows produces the same watermark.

Duplicates, identity collisions and malformed rows are reported, never silently repaired.

### A file that will not open

Files are only read together when their schemas are identical — reading a whole record with
`union_by_name` unifies mismatched column types instead, so one file whose price column came back
as text would retype *every* file's price as text.

A file that cannot be opened at all — a damaged footer, a truncated write, bytes that are not
Parquet — is **left out of the query entirely** rather than given a group of its own. A group that
cannot be read is still a branch of the union, so keeping it would take the readable history down
with it. Each candidate file is probed by pulling one real row, not merely described: describing
reads the footer, so a file whose footer survives but whose data pages are damaged would describe
cleanly and then fail halfway through the replay.

The count appears as `diagnostics.unreadableFiles` and raises `UNREADABLE_INPUT_FILES`. The file
is never repaired, never defaulted and never read as zeroes: its absence is a gap in the evidence.
The readable half of a record fingerprints identically with and without a damaged file beside it.

**When every file is unreadable**, the deliberate behaviour is an honest empty dataset rather than
a crash: zero events, the damaged files counted, and the run finishing with `INSUFFICIENT_HISTORY`.
From here that is indistinguishable from a record holding nothing, and both are things the replay
can say truthfully. What it must never do is continue as though some history had been recovered.

## Warm-up

Derived from the frozen contracts rather than chosen:

| Platform | Decides on | Also reads | Slowest | Warm-up (50 bars) |
| --- | --- | --- | --- | --- |
| CapitalBear | S5 | M1, M5 | **M5** | 4 h 10 m |
| IQ Option | M1 | M5, M10 | **M10** | 8 h 20 m |

`qfe-v2` reports `WARMING` below fifty closed bars (`READY_BARS`), and a primary close is only
half a decision — the ensemble reads higher-timeframe context snapshots as-of that close, and
those carry the same fifty-bar maturity rule. A run covering both platforms takes the longer of
the two: **30,000,000 ms**. `warmupDurationMs` may be set explicitly; leaving it unset derives
the number above.

Warm-up decisions never become measured trades, and they are refused **at creation** rather than
filtered out later, so there is no warm-up trade to exclude from a statistic. The feature state
warm-up produced is preserved into the evaluation window — resetting it at the boundary would
throw away exactly what was just paid for — and a test asserts a decision taken moments after
the window opens was made on indicators with more than fifty bars behind them.

Reported per run: `warmupEvents`, `warmupDurationMs`, `featuresReadyAt`.

## Evaluation window and settlement tail

```
warmupStart ──── evaluationStart ──────── evaluationEnd ──── settlementEnd
   feed only        measured decisions       no new             open trades
   no trades                                 selections          finish
```

`settlementEnd = evaluationEnd + max over platforms(maxEntryDelay + horizon + maxResolutionLag)`,
derived from `qst-paper-v1`: 15,000 ms for CapitalBear, 80,000 ms for IQ Option.

A selection made one millisecond before the window closes may wait its full entry bound for a
price, hold its whole horizon and settle at the outer edge of its resolution bound. Cutting the
feed at the window would throw that trade away — not because the market failed to answer, but
because the replay stopped listening — so the tail is fed and the trade is counted, because the
*selection* happened inside the window. Tail events may only finish what the window started: no
new intent is created after `evaluationEnd`.

## No lookahead

At replay index *N* the engine may know only events `0..N`.

* Input is strictly chronological and the clock is monotone.
* No completed candle is fed wholesale before its close: the replay feeds *observations*, and
  the existing Phase 5 builder forms every bar itself under the watermark.
* Entry is the first canonical eligible sample at or after `decisionAvailableAt` — never the
  pre-signal price, never a better one found later, never the board's close time.
* Expiry is the first canonical eligible sample at or after `entryTime + durationMs` — a price a
  millisecond before the horizon never settles a trade however favourable it looks.

`ReplayCausalityAudit` records this as evidence rather than as prose. Every persisted feature,
ensemble, board and paper trade passes through the replay's own storage sink, which asserts
against the clock at the moment it was produced:

| Field | Assertion |
| --- | --- |
| `latestFeatureAsOf`, `latestEnsembleAsOf`, `latestBoardAsOf` | never exceed `currentMarketTime` |
| `entryBeforeDecision` | `entryTime >= decisionAvailableAt` |
| `expiryBeforeTarget` | `expiryTime >= expiryTargetTime` |

The audit is a real check and not a tautology: a test feeds it records that claim things the
clock has not reached and requires it to say so.

Three behavioural tests back this up. A violent price move placed after a cut time cannot change
any outcome that settled before it; appending more history leaves every earlier outcome
untouched; and every entry and expiry is checked against the canonical sample stream rebuilt
through Phase 5's own `price_sample` gate.

## Identity and data quality

Asset, context and platform identity use the existing reset chain. A slot that changes asset
starts again from nothing — Phase 5 drops the series, Phase 6 discards every rolling indicator,
Phase 7 and 8 drop their history, and Phase 9 cancels the live trade — and a test drives a real
asset switch through a live slot and asserts the new series is as short as its own run.

CapitalBear slot 3 and IQ Option slot 3 are independent keys with independent everything.

Gaps stay gaps, degraded stays degraded, and an unusable reading is refused by Phase 5 exactly as
it would be live. The dataset summary reports `gapSeconds`, `gapShare` and the full quality
distribution before any performance number appears.

## Storage isolation

Everything a replay writes lives under:

```
<app-data>/market-data/replay/<replayRunId>/
    manifest.json      run.json      summary.json
    walk-forward.json  equity.json   evidence.json
    market/            ← paper trades, boards, ensembles, sandbox sessions
```

No live reader looks there. Phase 9's paper history, Phase 9.5's daily sessions and Phase 10's
analytics snapshots are read from their own directories, and a test seeds a live record, runs a
full replay against it and asserts every pre-existing file is byte-identical and every new file
is inside `replay/`.

The replay sink keeps the **evidence** — decisions and outcomes — and not the derived series:
observations, samples, seconds, candles and feature snapshots are exactly reproducible from the
source and the version contract, so writing hundreds of megabytes of them per run would spend
disk on something that is never the record of anything. It also refuses to hydrate warm-up
candles from stored history: a replay's warm-up must come from the events the replay actually
fed, or the first evaluated decision would rest on bars the run never saw.

## Session guard

The default is **disconnected**: a replay constructs its own `SessionGuard` with the settings it
was handed, and the live guard is a different object owned by a different engine in a different
process state. A replay cannot read, write, stop or unlock it, and a test asserts the live
permission, target, limit and daily total are byte-identical before and after.

`sessionGuardScenario` enables an optional sandbox study using `qst-session-guard-v1` semantics
*inside the replay only*. When it is set, the replay's paper layer consults that sandbox guard's
`canOpenNewEntry`, so a day that crosses its configured limit stops taking entries for the rest
of that day, exactly as a live day would. The report states days evaluated, days the target was
reached, days the limit was reached, days neither was, trades before the stop, time to trigger,
P/L at trigger and final P/L after already-open trades settled.

**No target is searched for.** No objective anywhere in this package is a function of a daily
target or limit, the report carries `optimized: false`, and a test asserts the model has no
field that could hold a "best" amount.

## Money

Simulated money is reported only when an explicit stake, payout rate and currency were
configured, and it is produced by `qst-analytics-v1`'s own `money_metrics`, which aggregates one
dominant currency and counts the rest as excluded. Nothing in this application converts between
currencies.

For monetary walk-forward evidence, all three periods must be priced, denominated in the **same**
currency, with `mixedCurrency == false` and `excludedByCurrency == 0`. Otherwise the verdict is
`MONETARY_UNVERIFIED` and directional analysis continues on its own.

Drawdown is reported as an absolute simulated figure. It is expressed as a *fraction* only when
`paperStartingCapital` was explicitly supplied — expressing a drawdown as a percentage of an
account nobody named would be inventing the account.

No broker payout is ever inferred, no wallet is read, and the equity series is labelled
`SIMULATED PAPER EQUITY`.

## Walk-forward

Rolling chronological folds. Two modes, both strictly ordered and neither shuffled:

* `DURATION` — rolling calendar windows, stepped by a configured duration. The preferred shape,
  because a fold is then a period of the market rather than a count of trades.
* `COUNT` — the ordered outcomes cut into equal contiguous segments; fold *i* takes segments
  `i .. i+trainSegments+1`. A weaker statement, and labelled as one wherever it appears.

Per fold: discover on TRAIN only using `qst-analytics-v1`'s own threshold search, freeze the
candidate, then *evaluate* it on VALIDATION and TEST. A future fold cannot reach a past one, and
appending history cannot change a calendar fold that has already been cut.

### Purge and embargo

This is the part that decides whether the numbers mean anything.

* **Membership is by expiry**, because that is when an outcome became knowable.
* **Purge is by `decisionAvailableAt`**, because that is when the completed decision first
  existed — never `boardAsOf`, which is only the close of the bar the cohort describes. The two
  are never the same instant: a board is assembled *after* the bar it is about has closed, so a
  selection can describe a bar from before the window and still have become actionable inside it.
  Purging on `boardAsOf` would throw that selection away as though it had leaked, when nothing
  about it was knowable before the window opened. Phase 9 introduced `decisionAvailableAt` for
  exactly this distinction and prices every entry from it.
  A decision available at the very first instant of a window is **inside** it: the bound is
  inclusive, matching the way Phase 9 admits an entry at `decisionAvailableAt` itself.
* **Embargo**: a gap the width of the longest possible trade lifetime is inserted at every
  boundary, derived from `qst-paper-v1` — `maxEntryDelay + horizon + maxResolutionLag`, which is
  15,000 ms for CapitalBear and 80,000 ms for IQ Option. Rows falling inside a gap belong to no
  period at all.

Together these guarantee no single trade can have its decision on one side of a split and its
outcome on the other, and a test asserts the three periods are disjoint and that every
validation and test row was decided after the previous period closed.

### What a fold reports

`foldId`, the six boundaries, `purgeMs`, `embargoMs`, `purgedRows`, `embargoedRows`,
`candidateSourceSnapshotId` (a deterministic id for the exact training evidence),
`candidateMetric`, `candidateThreshold`, `comparisonsEvaluated`, the three periods' metrics,
`directionalStable`, `monetaryStable`, `monetaryVerdict` and warnings.

`NO_STABLE_CANDIDATE` is a valid result. Phase 10's sample floors are never lowered to produce
one, and a test asserts a thin record returns nothing rather than a rescued finding.

The summary has **no best-fold field**. A walk-forward study whose headline is its best fold is a
walk-forward study that learned nothing.

## Latency sensitivity

Each delay is a **separate complete replay** of the same history with exactly one thing changed:
when the decision is treated as actionable. The Phase 7 opinion, the Phase 8 board and the
deterministic paper trade ids are identical in every scenario, so a difference in the table is a
difference in what the market did next and never a difference in what the analysis said. A test
asserts the selection set and every signal field match across delays.

The table is labelled `SIMULATION ONLY` and is a **robustness test, not a tuning knob**. A delay
that happens to look better on one history is a property of that history; nothing here chooses a
production delay and there is no setting it could be written to.

## Payout sensitivity

Optional, research only, and only when an explicit stake was configured. The same recorded
outcomes are re-priced at stated payout rates with no direction changed, and the break-even win
rate — `1 / (1 + payout)` — is printed beside every scenario so an apparent directional edge can
be read against the payout it would actually need.

## Coverage and robustness

Reported before any performance claim, because a profitable backtest over one regime, one asset
and one afternoon is a description of one regime, one asset and one afternoon:

* regime counts over **every** classification the pipeline produced, not only the traded ones;
* asset share, with `ASSET_CONCENTRATION_WARNING` when one asset dominates — and no automatic
  removal;
* hours of day, weekdays and distinct trading dates actually covered;
* rolling windows over consecutive outcomes, to show whether an edge was stable, decaying or
  episodic;
* contribution by platform, asset, regime and direction — descriptive, never a whitelist;
* per-day distribution in an explicitly named timezone, with no claim of daily income;
* maximum win and loss streaks, which nothing sizes anything from.

There is deliberately no single `BACKTEST_SCORE`.

## Warnings

`INSUFFICIENT_HISTORY`, `LOW_RESOLUTION_RATE`, `LOW_SELECTION_COUNT`, `LOW_ASSET_SAMPLE`,
`SINGLE_REGIME_DOMINANCE`, `LIMITED_REGIME_COVERAGE`, `NARROW_TIME_COVERAGE`,
`ASSET_CONCENTRATION_WARNING`, `HIGH_GAP_RATE`, `MIXED_CURRENCY`, `MONETARY_UNVERIFIED`,
`PAPER_ACCOUNTING_UNAVAILABLE`, `MULTIPLE_TESTING_WARNING`, `OUT_OF_SAMPLE_DEGRADATION`,
`PARAMETER_INSTABILITY`, `INSUFFICIENT_FOLDS`, `NO_STABLE_CANDIDATE`, `SINGLE_PLATFORM`,
`STRATEGY_EVIDENCE_TRUNCATED`, `SYNTHETIC_BEHAVIOR_TEST`, `MALFORMED_INPUT_ROWS`,
`DUPLICATE_INPUT_ROWS`.

## Real history versus synthetic fixtures

They are never combined, and their sample counts are never added.

* **Real history** tests empirical historical behaviour. It carries `sourceMode: REPLAY`.
* **A synthetic fixture** tests software correctness and edge cases. Every replay of one carries
  `SYNTHETIC_BEHAVIOR_TEST`, which is never removed and never softened: a synthetic result is a
  statement about the software and about no market.

## Determinism

Running an identical manifest over identical history twice produces an identical `replayRunId`,
event count, ensemble count, board count, paper trade ids, entries, expiries, W/L/D, analytics
fingerprint and walk-forward result. Only the runtime diagnostics differ.

Feeding the same record one event at a time or a thousand at a time produces identical output:
the driver advances the watermark and ingests strictly per event, so batching cannot reach the
semantics. And because the durable record is an unordered set that `source.py` sorts, reversing
the file enumeration produces the identical canonical stream and the identical fingerprint.

## Performance

Replay advances as fast as the CPU allows and never sleeps for a historical interval; the
benchmark prints market-minutes-per-wall-second beside the throughput and asserts it exceeds one.

```bash
python services/quant-engine/tests/benchmark_replay.py 100000
python services/quant-engine/tests/benchmark_replay.py 1000000
```

Neither size runs in CI — a million events is several minutes of CPU and would make every pull
request pay for it. They are developer acceptance runs. The measured results are recorded in
[Development](development.md#phase-11-replay-benchmark): on this machine, **4,080 events/second at
100k** and **3,196 events/second at 1M**, both COMPLETED with zero causality violations, and peak
memory of 668 MB and 1.79 GB — bounded rather than proportional, since ten times the events cost
2.7 times the memory and the per-event figure falls.

The benchmark's source *generates* its rows rather than holding them, so the measurement is of
the engine and not of the fixture, and a record larger than memory is genuinely exercised. The
Parquet reader streams in bounded batches with the sort done inside DuckDB, which spills to disk
when it has to.

Bounded memory in the replay itself: strategy evaluations are retained to `EVALUATION_MEMORY`
(250,000) and paper trade rows to `TRADE_ROW_MEMORY` (200,000); exceeding the first raises
`STRATEGY_EVIDENCE_TRUNCATED` rather than growing without limit.

## API

Local-only, and refused outright for any browser origin.

```
POST /api/replay/runs                       start (202, or 409 when one is already running)
GET  /api/replay/runs                       list jobs and stored runs
GET  /api/replay/runs/{id}                  status and progress
GET  /api/replay/runs/{id}/summary          the baseline backtest
GET  /api/replay/runs/{id}/walk-forward     the folds
GET  /api/replay/runs/{id}/equity           SIMULATED PAPER EQUITY
POST /api/replay/runs/{id}/cancel           stop the offline run, and only that
```

At most **one** heavy replay at a time. A second is refused rather than queued: two replays
competing for the same cores would cost the live capture loop its samples, and no result is worth
that. A replay runs on its own worker so a progress poll answers immediately, and progress is
runtime-only — the replay advances on recorded events whether or not anybody is watching.

Cancelling stops the offline replay. It does not stop capture, does not touch the live trading
day, does not disarm anything and could not reach a broker control if it tried.

**Cancellation is sticky for the whole job, not for the phase that happened to be running.** A
replay runs a baseline first and then one research scenario per configured latency delay, and the
failure this rule exists to prevent is quiet and plausible: the baseline finishes, the operator
cancels, the scenarios never run, and the job reports `COMPLETED` on the strength of half the work
that was asked for. So:

* the flag is read again at every phase boundary and nothing resets it;
* no later scenario begins once it is raised;
* a scenario that did not finish is dropped rather than reported half-measured beside the ones
  that did;
* the job's final status is `CANCELLED` however much of it completed;
* the baseline work is kept — throwing away what an operator paid for helps nobody — but the
  summary carries `partial: true` and `CANCELLED_PARTIAL_RESULT`, and the desktop panel says so
  in a banner above the numbers.

A failed run is persisted as `FAILED` with its reason. No partial result is ever presented as a
completed one.

## CLI

```bash
python -m quant_engine.replay --from <epoch-ms> --to <epoch-ms> \
  --platform both --warmup 0 --folds 3 --output report.json
```

Also `--data-dir`, `--latency` (repeatable), `--currency/--stake/--payout`,
`--starting-capital`, `--timezone`, `--no-walk-forward`. A backtest has to be runnable, diffable
and re-runnable from a terminal; requiring a UI to validate a research layer would make the
research layer harder to check than the thing it is checking.

## Desktop panel

A compact research panel in the trading window: history window, platform, warm-up, an optional
latency study, run and cancel, live progress, then the dataset, both platforms' baselines,
coverage, the fold table and the latency table.

There is **no Apply button**, no bridge method that would accept a threshold and no engine
endpoint that would take one. A test scans the panel's code — with comments stripped, so the
prose explaining that nothing is applied can neither pass nor fail the check — and asserts it
contains no word that could reach an order, a control, an arm state or a stake.

## Phase 12 evidence boundary

`ReplayEvidence` is persisted per run: baseline performance, the walk-forward folds, stable
directional and monetary candidates, stable regimes and strategy-regime observations, latency
sensitivity, coverage, warnings and the full version contract, with `researchOnly: true` and
`appliedToLiveExecution: false` as literals.

**Nothing reads it.** Persisting it without a consumer is the point: Phase 12 is the first phase
allowed to *consider* it, and it will inherit evidence instead of starting from an empty table.
A test searches the whole repository — engine, desktop and shared packages — for a consumer and
asserts there is none.

## What a replay may never say

Even a positive result is a statement about one dataset over one date range with one sample size
and one set of data-quality caveats. "The bot is profitable" is not a sentence this layer can
support. "On dataset X over dates A–B, the frozen `qst-*` versions produced historical paper
expectancy Y over N resolved outcomes, with interval Z and warnings W" is.

## Version contract

| Layer | Version |
| --- | --- |
| Features | `qfe-v2` |
| Regime | `qst-regime-v1` |
| Strategy | `qst-strategy-v1` |
| Ranking | `qst-ranking-v1` |
| Paper | `qst-paper-v1` |
| Analytics | `qst-analytics-v1` |
| Replay | `qst-replay-v1` |

Every replay result records all seven. The supported upstream versions are literals in
`replay/models.py` rather than imports, so a later contract arrives as an explicit mismatch
instead of silently pooling new decisions with old outcomes.
