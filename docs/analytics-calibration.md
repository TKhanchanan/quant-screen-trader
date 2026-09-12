# Outcome analytics and score calibration (Phase 10)

`qst-analytics-v1`

Phase 9 answers *what did the market do after that decision?* This layer answers the only
question that follows from a few thousand of those answers:

> Do the scores produced by Phase 7 and Phase 8 actually correspond to better outcomes?

```
Phase 6 features ─► Phase 7 ensemble ─► Phase 8 board ─► Phase 9 paper outcome
                                                              │
                                                              ▼
                                                    ┌──────────────────────┐
                                                    │ resolved outcomes    │
                                                    │ (durable Parquet)    │
                                                    └──────────┬───────────┘
                                                               ▼
                        ┌──────────────────────────────────────────────────┐
                        │ Phase 10                                         │
                        │  dataset ─► calibration ─► segmentation          │
                        │                        └─► threshold research    │
                        └──────────────────────────────────────────────────┘
                                                               │
                                                               ▼
                                                  report / snapshot / panel
                                                               ╳
                                              no path back into any decision
```

## It measures. It does not act.

The one rule the whole layer exists to protect:

| Wrong | Right |
| --- | --- |
| "rankScore 0.58 looks better, set `MIN_SELECTION_SCORE` to 0.58." | "Outcomes at `rankScore >= 0.58` differed materially from the ones below it. Reported as a candidate." |
| "TREND_UP wins more, disable RANGE." | "TREND_UP: n=180, 62.4% (54.9–69.3%). Descriptive." |
| "We are behind target, loosen the gate." | Phase 10 has never heard of a target. |

Phase 10 is the first layer that can tell which threshold *would have* worked, which makes it the
one with the most obvious temptation attached to it. So the boundary is enforced rather than
described. `test_analytics_safety.py` scans every name the package binds and fails on
`executionManager`, `orderExecutor`, `pressPoint`, `sendInputEvent`, `press`, `arm`, `disarm`,
`higher`, `lower`, `stake`, `martingale`, `positionSize` — and equally on `minSelectionScore`,
`minLeadMargin`, `baseWeights`, `dailyProfitTarget`, `dailyLossLimit`, `canOpenNewEntry` and
`saveSettings`, because a measurement layer that can spell the setting it just formed an opinion
about is one refactor away from setting it.

There is no `POST` on the analytics API, no "Apply threshold" button in the panel, and no code
anywhere in the application that reads a `ThresholdCandidate`.

## Canonical input

Resolved Phase 9 outcomes, and nothing else.

| Status | Enters the win rate | Counted where |
| --- | --- | --- |
| `RESOLVED` with `WIN` / `LOSS` / `DRAW` | yes | every metric |
| `PENDING_ENTRY`, `OPEN` | no | data-quality report |
| `CANCELLED` | no | data-quality report |
| `INVALID` | no | data-quality report |

A cancelled trade is **not a loss** — the identity it belonged to stopped existing, and the market
never answered the question. An unresolved trade is not evidence of anything yet.

Three things happen before any number is computed:

1. **Collapse.** The durable record is append-only: one row per lifecycle transition. Each trade
   is reduced to the state it actually ended in.
2. **Version check.** All five upstream contracts are checked — `qfe-v2`, `qst-regime-v1`,
   `qst-strategy-v1`, `qst-ranking-v1`, `qst-paper-v1`. Anything else is excluded, counted, and
   its version string reported. Two definitions never pool silently.
3. **Sort.** By `expiryTime`, then `paperTradeId`. Nothing depends on filesystem iteration order,
   so the same stored history always produces the same dataset.

Phase 7 strategy votes are joined back onto the outcomes they produced, on
`(platform, slotId, contextId, boardAsOf)`. A slot whose asset changed carries a new context id
and therefore cannot lend its votes to the previous asset's results.

## Data quality comes first

Reported before any performance claim, and for one reason:

> A 60% win rate over the fifth of decisions that happened to resolve is not a 60% win rate.

`resolvedRate = resolved / eligible`. Below 0.5 the snapshot raises `LOW_RESOLUTION_RATE`. Entry
timeouts, resolution timeouts, context cancellations, unsupported versions and malformed rows are
each counted and named.

## Why `rankScore` is not a probability

Phase 8's `rankScore` orders the opportunities that existed at one moment. It was produced by a
layer that had never observed an outcome, so it has never claimed that 0.70 means "wins 70% of
the time" and it is not being graded against that claim.

Phase 7's `ensembleConfidence` is a measure of how much usable, agreeing evidence the strategy
panel had. Same story.

Consequences, both deliberate:

* every bin table is labelled an **empirical outcome curve**, never a probability calibration;
* there is **no Brier score** anywhere in the package, and a test asserts its absence. Scoring an
  uncalibrated ordering as if it were a probability would put a number on a claim nobody earned.
  A Brier score becomes available if and when an explicit probability estimator exists.

## Score bins

Fixed for `qst-analytics-v1`, bin count configurable:

```
[0.00, 0.10)  [0.10, 0.20)  ...  [0.80, 0.90)  [0.90, 1.00]
```

Half-open upward, closed at the very top so a score of exactly 1.0 belongs somewhere. A value sits
in the first band whose upper edge it is strictly below — compared against the same
`(index + 1) / count` expression that defines the edge, not computed as `int(value * count)`,
because `0.3 * 10` is `2.9999999999999996` in IEEE 754 and the shortcut puts 0.3 one band too low.

Empty bands are returned rather than skipped: a curve that omitted the bands nothing scored in
would look far better covered than it is.

## Money is never pooled across currencies

A slice's money total is denominated in **exactly one currency**, named on the record beside it.
When a history contains more than one — an operator changed the simulated currency mid-run — the
most-represented one is aggregated, the rest are counted in `excludedByCurrency` and left out of
every monetary number, and the snapshot raises `MIXED_CURRENCY`.

Nothing in this application converts between currencies, exactly as Phase 9.5 refuses to. The
excluded outcomes still contribute their direction to the win rate; only their money is dropped.

> Pooling ฿40 and −$50 into "−10" produces a number that is not wrong in any single row and is
> meaningless as a whole — the one number an operator is most likely to act on and least able to
> check.

A tie in representation is broken alphabetically so the choice is reproducible, and a row that
carries a realized result with no currency label is never the one that gets counted.

## Wilson intervals

Every win rate carries a 95% Wilson score interval. Wilson rather than the normal approximation,
because on the sample sizes a fresh installation actually has — eight resolved outcomes, six of
them wins — the normal approximation produces bounds above 1.0, and an interval that claims
impossible values is worse than no interval.

`57.9%` over thirty outcomes and `57.9%` over three thousand are different statements. Only the
interval says which one is on screen.

## Monotonicity, correlation, and reporting failure

For each score the layer reports:

* **Spearman rank correlation** between the score and a binary outcome. Draws are excluded and the
  number excluded is stated — a draw is neither a win nor a loss, and folding it in as a half
  would invent an outcome the market never produced.
* **Bin-level ordering**: whether win rate is non-decreasing across the bands that carry at least
  `MIN_DISPLAY_SAMPLE` outcomes, plus the rank association between band position and band rate.

Thin bands cannot decide the ordering flag. Strict non-decreasing order across ten bands never
survives real noise, and a warning that fires on every dataset is a warning nobody reads.

**The most valuable thing this layer can report is that a score does not work.**

| Warning | Meaning |
| --- | --- |
| `NON_MONOTONIC_RANK_SCORE` | Higher `rankScore` did not come with better outcomes. |
| `NON_MONOTONIC_CONFIDENCE` | Same for `ensembleConfidence`. |
| `INVERSE_RANK_SCORE` | Higher `rankScore` came with **worse** outcomes. |
| `INVERSE_CONFIDENCE` | Higher confidence came with **worse** outcomes. |

These are surfaced prominently and are never softened. A non-monotonic score may mean the score
design is weak, the sample is too small, regimes are mixing, or the capture is degraded. Phase 10
reports which of those is *possible*; it does not fix any of them, and it does not reinterpret the
metric until the metric looks good.

## Segmentation

Every table carries its own sample size. Nothing is hidden below a floor; it is labelled.

| Floor | Value | Effect |
| --- | --- | --- |
| `MIN_DISPLAY_SAMPLE` | 20 | below this a segment is labelled `LOW_SAMPLE` |
| `MIN_ASSET_SAMPLE` | 30 | below this an asset is never placed in a performance ordering |
| `MIN_RECOMMENDATION_SAMPLE` | 50 | below this no threshold candidate is produced at all |

* **Platform** — CapitalBear S5 and IQ Option M1 are reported separately and never pooled into one
  unlabelled number. Different horizon, different microstructure, different capture cadence: a
  single combined win rate would be the average of two answers to two different questions.
* **Asset** — keyed on `platform + assetName`. The same string names two different instruments on
  two brokers.
* **Regime**, and **regime × direction**, because a layer that reads an uptrend well is not
  automatically good at reading a downtrend.
* **Hour and weekday** — in an explicit timezone, `Asia/Bangkok` by default, never the host's. An
  hour table that silently followed the machine would describe a different trading session on a
  laptop that crossed a border.
* **Agreement**, **lead margin**, **regime confidence** — each in its own bands. A board with one
  directional candidate has `leadMargin = null` and is excluded rather than treated as a margin of
  zero; it did not win by nothing, it had nobody to win against.
* **Rank × confidence grid** — the interesting cells are off-diagonal. A score pair that only ever
  agrees with itself carries one piece of information, not two.
* **Input quality** — `entryQuality` and `boardStatus`, read exactly as Phase 5 and Phase 8
  recorded them. If degraded capture is where the losses live, the fix is in the capture layer and
  not in a score.

## Strategy contribution and the strategy × regime matrix

Each Phase 7 strategy's vote is read exactly as Phase 7 recorded it and reinterpreted in no way.
For each strategy: votes present, agreed, disagreed, abstained (`NEUTRAL` and `SKIP` counted
separately), and the outcomes in each case.

The matrix scores each cell over the outcomes where the strategy **agreed with the selection that
was actually taken** — the only version of the question with an answer. A strategy that abstained
contributed no evidence, and crediting or blaming it for a decision it declined to take part in
would punish it for being honest about its coverage.

## Train / validation / test

Chronological. Earliest 60% to TRAIN, next 20% to VALIDATION, most recent 20% to TEST — ratios
configurable, ordering not.

**Never shuffled.** Market history is a sequence; a random split lets a threshold learn from trades
that had not happened yet, and the resulting number looks exactly like evidence. A test asserts the
threshold module binds no `shuffle`, `sample`, `choice` or `random` at all.

Phase 10 trains no production model. The split exists to check whether anything found is stable.

## Threshold research

Candidate metrics: `rankScore`, `ensembleConfidence`, `agreement`, `regimeConfidence`,
`leadMargin`. Operator `>=`, on a fixed grid.

Three rules keep the output from being wishful thinking:

1. **Searched on TRAIN only**, then *evaluated* on VALIDATION and TEST. A threshold chosen with
   test data in view has already used the data it is about to be judged by.
2. **Coverage is part of the objective.** `coverage = passing / all resolved`. A rule that wins 90%
   of the time on three trades has a coverage of 0.005 and is not a finding.
3. **Win rate alone is never the objective.** Sample size, coverage, expectancy and consistency
   across all three periods all have to hold.

The research ordering is `lift × √coverage`, where `lift` is the threshold's win rate minus the
*same period's own* baseline — so a rule is judged against the market it actually traded in rather
than against a pooled average of periods it never saw. The square root is a documented compromise,
fitted to nothing; it exists so a rule that fires four times cannot outrank one that fires four
hundred on a fractionally smaller edge. **It is a research ordering, not a production objective.**

### Stability: directional and monetary are reported separately

A threshold gets two verdicts, because they disagree and the disagreement is the finding.

| | Measures | `STABLE` when |
| --- | --- | --- |
| `directionalStability` | win rate | positive lift over the period's own baseline in all three |
| `monetaryStability` | expectancy | positive expectancy per trade in all three |

> At a 0.8 payout a rule has to win about **55.6%** of the time to break even. A threshold that
> lifts the win rate from 52% to 55% in *every* period is directionally stable and loses money in
> all three.

One combined flag would report that as a finding. The overall verdict requires **both** whenever
both can be measured; when Phase 9 priced nothing the monetary verdict is `UNTESTED` and the
candidate carries `MONETARY_UNVERIFIED` — directional evidence is still evidence, and discarding
it would be as wrong as promoting it.

| Overall verdict | When |
| --- | --- |
| `STABLE` | TRAIN ≥ 50 passing, VALIDATION and TEST ≥ 20 each, and both verdicts clear |
| `UNSTABLE` | the direction or the expectancy did not hold — the failing periods are named |
| `UNTESTED` | the out-of-sample periods were too small to have tested anything |

Train good, validation good, test bad is `UNSTABLE` and is not recommended. `UNTESTED` is a
different statement from "it failed" and is kept distinct.

### No target chasing

No objective anywhere in this layer knows about a daily profit target, a loss limit, or a number of
trades needed to reach one. Phase 9.5 owns session risk; optimizing a score toward a money goal is
the exact failure the separation between those two layers exists to prevent. A test asserts the
threshold module cannot name any of them.

## Overfitting and multiple testing

Every snapshot reports `comparisonsEvaluated` — how many threshold comparisons were searched to
produce the table — and raises `MULTIPLE_TESTING_WARNING` whenever that is more than one.

> The best of ninety-five comparisons on one history is the best of ninety-five comparisons on one
> history.

Every "best asset", "best hour" and "best regime" table is explicitly descriptive and carries its
sample size. Nothing is automatically whitelisted or blacklisted, because there is no mechanism in
this layer that could.

## Determinism

* **Dataset fingerprint** — SHA-256 over **every field of every row**, in declaration order,
  including the joined Phase 7 votes. Not a curated list of "fields a metric uses": a curated
  list drifts, and a field added for one table and forgotten in the hash would let two materially
  different datasets share a snapshot id. A test mutates each field in turn and asserts the
  fingerprint changes, which is only possible because the hash is exhaustive. Never over a
  modification time, which changes when nothing did and stays put when an outcome is rewritten.
* **Snapshot id** — UUID5 over `analyticsVersion | datasetFingerprint | settingsFingerprint`. The
  same history analysed twice is the same snapshot; one changed outcome is a different one.
* **No clock.** Nothing in the package can see what time it is now. `datetime` appears only to put
  an outcome in its local hour bucket.
* **Seeded bootstrap.** Optional, off by default. An interval that moved on every refresh would
  invite rerunning until it looked narrow.

## Invalidation

A cached analysis describes the record it was built from, and that record grows while the
application runs. So the moment Phase 9 resolves an outcome, `persist_paper` marks the analysis
stale — one assignment on the ingestion path, no work — and the **next read rebuilds**.

Correctness over caching. A snapshot that silently describes a record from three hours ago is not
a cheaper answer, it is a different question's answer, and the panel would report a win rate that
stopped being true mid-session with no sign of it.

* Only a **resolved** transition invalidates. A pending or open trade changes nothing an analysis
  measured, and rebuilding on every lifecycle write would make diagnostics cost the capture layer
  its samples.
* A restart marks stale too: outcomes resolved while the process was down are new to the analysis.
* A failed rebuild leaves the flag up so the next read tries again, and keeps the previous
  snapshot rather than replacing a real answer with an empty one.
* `stale` and `pendingOutcomes` are on every API response and in the panel, so a rebuild that has
  not happened yet is visible rather than silent.

## Storage

Snapshots are written as JSON under `market-data/analytics_snapshots/`, named by sample end time
and snapshot id so they sort by name rather than by mtime. Bounded to 200; they are cheap to
regenerate and are never the source of truth.

They are kept **beside** the market record, never inside it: the Parquet categories hold rows the
engine measured, and filing a derived analysis among them would let a later reload treat an opinion
about the data as more data. No `PaperTrade` is ever modified.

## API

Local-only, read-only, busy-safe. A browser origin gets `403`; an engine mid-ingest or an analysis
mid-rebuild gets `429` rather than a half-built answer.

```
GET /api/analytics/summary
GET /api/analytics/calibration/rank
GET /api/analytics/calibration/confidence
GET /api/analytics/regimes
GET /api/analytics/strategies
GET /api/analytics/assets
GET /api/analytics/time
GET /api/analytics/thresholds
GET /api/analytics/snapshot
GET /api/analytics/export        # format=json|csv, bounded
```

Filters: `platform`, `assetName`, `regime`, `direction`, `from`, `to`. Field equality and a time
window — there is no expression language, and an unknown value is refused with `422`.

A filtered view rebuilds from the cached history rather than slicing a finished snapshot, so its
data-quality report describes the same population as the metrics beside it.

Every response carries `analyticsVersion`, `sampleCount`, and `researchOnly: true` /
`appliedToLiveExecution: false`. The export column list is an explicit allow-list: market
measurements only, with no session, credential, calibration or window identity in it.

## Desktop panel

Last in the trading window, collapsed by default, polled every 15 seconds. It shows overall
outcomes with data quality, both calibration curves, the regime table, the strategy × regime
matrix, best and worst assets among those with enough sample, the hourly table with its timezone
named, and the threshold research table.

There is no button in it. Thin slices are dimmed rather than dropped, unstable thresholds are
visually separated from stable ones, and the "not applied to live execution" notice appears twice —
once at the top and once beside the thresholds.

## Warnings

`INSUFFICIENT_SAMPLE` · `LOW_RESOLUTION_RATE` · `LOW_SAMPLE_SEGMENTS` ·
`NON_MONOTONIC_RANK_SCORE` · `NON_MONOTONIC_CONFIDENCE` · `INVERSE_RANK_SCORE` ·
`INVERSE_CONFIDENCE` · `THRESHOLD_UNSTABLE` · `MULTIPLE_TESTING_WARNING` · `VERSION_MIXED` ·
`MIXED_CURRENCY` · `PAPER_ACCOUNTING_UNAVAILABLE` · `NO_STRATEGY_EVIDENCE` · `SINGLE_PLATFORM`

## Output for a later adaptive phase

Each snapshot persists a small `research` list — stable score bands, regime observations,
strategy-regime relationships and candidate skip conditions — so a future adaptive phase inherits
evidence rather than starting from an empty table.

**`stable` means the same thing here as it does for a threshold**, and is earned the same way.
A regime and a strategy pairing are slices chosen *after* seeing the history they describe, which
is the same selection bias a threshold search has, so they are held to the same standard:

* a pooled effect must exist at all — Wilson lower bound above 0.5 (favourable) or upper bound
  below it (a candidate skip condition) — otherwise the finding records `NO_POOLED_EFFECT`;
* the effect must hold in **every** chronological period against *that period's own baseline*;
* each period must carry at least `MIN_DISPLAY_SAMPLE` outcomes, and the pooled slice at least
  `MIN_RECOMMENDATION_SAMPLE`.

Every finding reports `trainCount` / `validationCount` / `testCount` and the win rate in each, so
a reader can check the claim rather than take it. A regime that looks excellent pooled and only
ever won in the first third of the history is recorded as an observation with `NOT_HELD_IN_TEST`
named — never as stable. That shape is exactly what a decayed edge looks like, and a tight pooled
interval is the most convincing thing it can produce.

Every finding carries `appliedToLiveExecution: false` as a literal, and **nothing in this
application reads one.** Persisting them without a consumer is the point.

## What this layer never claims

No output of Phase 10 says guaranteed profit, always profitable, or safe. Every rate is stated with
its sample size and its interval, and a rate over a few hundred outcomes describes what was
recorded — not what will happen next.

## Walk-forward

Phase 10 provides `folds()`, which cuts an ordered dataset into successive chronological windows
without reordering it — the reusable piece a replay will need. It implements no replay of its own.
Full historical replay and backtesting are Phase 11.
