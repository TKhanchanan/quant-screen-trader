# Opportunity ranking

Phase 6 produces facts. Phase 7 interprets one market. Phase 8 compares several Phase 7 opinions against each other and answers one question: *among the markets this application is currently observing, which analytical setup is strongest relative to the others?*

It does not answer "place this trade". There is no order, stake, bankroll, martingale, payout, expected value or broker control anywhere in `quant_engine.opportunity` or its read API, and two tests enforce that: one scans every name the package binds or calls for execution vocabulary, and one asserts the package's entire import surface is `quant_engine`, the standard library and Pydantic — so there is no networking, no subprocess and no IPC in scope to reach a broker with.

## Architecture

`quant_engine.opportunity` holds the whole layer, deliberately small: `models.py` carries the wire schema, the version constants and the code vocabularies; `scoring.py` the ranking arithmetic, the stability primitives and the two selection gates; `engine.py` the epoch cohorts, the board assembly and the event entry point. `opportunity_api.py` is the local-only read surface.

```
Phase 5 candle → Phase 6 FeatureSnapshot → FeatureBundle → Phase 7 EnsembleSnapshot → Phase 8 board
```

## Input contract

Phase 8 consumes canonical Phase 7 `EnsembleSnapshot` objects and nothing else.

It reads no broker price, no `MarketObservation`, no `FeatureBundle` and no raw indicator. It recomputes no candle, EMA, RSI, ATR, MACD or regime, and it holds no copy of a Phase 7 strategy. The package imports `quant_engine.strategy.models` — the wire schema — and no other Phase 6 or Phase 7 module; `test_opportunity_safety.py` asserts exactly that, module by module.

The reason is that two definitions of one fact can disagree. If ranking recomputed a trend score it would eventually rank a market on evidence Phase 7 never saw, and no replay could reconstruct why. Phase 6 is the only source of facts; Phase 7 is the only source of opinions; Phase 8 only orders opinions that already exist.

## Version contract

```
featureVersion   qfe-v2            (required, not merely reported)
regimeVersion    qst-regime-v1     (required)
strategyVersion  qst-strategy-v1   (required)
rankingVersion   qst-ranking-v1
```

Every `OpportunityCandidate` and `OpportunityBoard` carries all four, and every persisted row carries them too.

The three supported upstream versions are literals in `opportunity/models.py` rather than imports of the Phase 6/7 constants, for the same reason Phase 7 pins `qfe-v2` as a literal: this layer's gates are calibrated against what qst-strategy-v1 confidence actually means, and tracking the upstream constant would let a later contract flow silently into a ranking nobody re-checked. A snapshot carrying any other version is excluded with `UNSUPPORTED_VERSION` and is never ranked, never entered into a stability window, and never counted as usable evidence. A future strategy version is refused loudly, not consumed.

## Platform-separated boards

There is one board for CapitalBear and one for IQ Option. There is deliberately no combined 1–18 ranking.

CapitalBear settles five-second bars; IQ Option settles one-minute bars. A single global ordering would place an S5 opportunity directly against an M1 opportunity and imply the two decisions share a horizon. They do not. The desktop may show both boards at once; they remain separate rankings, each with its own rank 1.

`PRIMARY_HORIZON` in `opportunity/engine.py` restates which close defines an epoch on each broker. It is a literal rather than an import of the Phase 5/6 table for the same reason as the versions: the epoch *is* the meaning of the comparison, so a horizon change upstream must arrive here as an explicit `INVALID_IDENTITY` exclusion rather than silently redefining what a board compares.

## The same-`asOf` cohort rule

`board.asOf` is one platform's primary close time. Only ensembles carrying exactly that `asOf` belong to that cohort.

Nine slots never arrive in the same CPU millisecond, and that does not matter — what matters is that they describe the same *market* decision time. A CapitalBear board at `10:03:25` ranks only the snapshots stamped `10:03:25`. A slot still carrying `10:03:20` is not weak; it is owed. It is recorded on the board as an `EXCLUDED` candidate carrying `STALE_FOR_EPOCH`, it does not count toward `receivedSlots`, and it is replaced by a real candidate the moment its current ensemble arrives.

### Expected slots

The expected cohort is the set of slots the live observation pipeline is actually carrying — `MarketEngine.expected_slots`, which is the slots that have a builder. A disabled or unassigned slot has never produced an observation, so it is simply absent and cannot hold a board at `COLLECTING` forever. If five CapitalBear slots are enabled, `expectedSlots` is five, not nine. A slot that disappears from the expected set stops being awaited, while a slot that has already delivered this epoch is never dropped from the count.

### Board status

| Status | Meaning |
| --- | --- |
| `COLLECTING` | Some expected slot has not produced its same-time ensemble yet. Candidates are ranked, but provisionally: no leader is named, because a stronger market may still be on its way. |
| `READY` | The cohort is complete and a leading analysis was resolved. |
| `PARTIAL` | The epoch was superseded before every expected slot reported. What arrived is ranked and reported honestly; `missingSlots` names the rest. Nothing is fabricated for a slot that never spoke. |
| `NO_OPPORTUNITY` | A complete cohort in which nothing cleared the conservative selection gate. |
| `INVALID` | Every candidate that arrived failed a version, identity or chronology check, so there is nothing in the cohort that was measured under a contract this layer knows. |

Precedence is deliberate: integrity first, then incompleteness, then the selection outcome. The most important caveat is the one reported.

An epoch is finalized when the next epoch for that platform begins. That is the moment the board stops being able to change, and therefore the moment it is written to Parquet — exactly one immutable row per epoch.

## What `rankScore` is

`rankScore` is a relative utility for ordering the opportunities that exist right now. Nothing else.

It is **not** a probability, a win rate, an expected return, an edge or a payout-adjusted anything. This system has never observed an outcome, so nothing in it could have been calibrated against one. Phase 8 makes no claim that a candidate scoring 0.55 performs better than one scoring 0.40 — only that, on the evidence Phase 7 produced, it is the stronger current setup. Establishing whether that relationship holds is Phase 10's work, not this layer's.

There is no probability calibration here: no Platt scaling, no isotonic regression, no Brier score, no reliability curve. There is no expected value: broker payout never enters the layer. And there is no cross-asset correlation model — if EUR/USD and GBP/USD both rank highly, both are shown honestly, and whether correlated simultaneous signals need special handling is a question outcome analytics can answer later.

## Score formula

Phase 7 confidence already contains agreement, regime, quality, breadth and each strategy's conviction. Rebuilding that here with different weights would just be Phase 7 again, so `ensemble.confidence` is the dominant term and everything else is a bounded multiplier:

```
breadth      = min(activeStrategies / BREADTH_FULL, 1)

supportBase  = AGREEMENT_WEIGHT           × agreement
             + REGIME_CONFIDENCE_WEIGHT   × regime.confidence
             + BREADTH_WEIGHT             × breadth
             + PERSISTENCE_WEIGHT         × directionPersistence3

penalty      = DISAGREEMENT_PENALTY × ramp(disagreement, DISAGREEMENT_TOLERANCE, DISAGREEMENT_CEILING)
             + NOISE_PENALTY        × ramp(noiseScore,   NOISE_TOLERANCE,        NOISE_CEILING)
             + DEGRADED_PENALTY     when the Phase 7 analysis status is DEGRADED

supportScore = clamp(supportBase − penalty, 0, 1)
multiplier   = SUPPORT_FLOOR + (1 − SUPPORT_FLOOR) × supportScore
rankScore    = clamp(ensemble.confidence × multiplier, 0, 1)
```

### Named weights

| Constant | Value | What it is |
| --- | --- | --- |
| `AGREEMENT_WEIGHT` | 0.35 | How hard the whole eligible panel leaned one way |
| `REGIME_CONFIDENCE_WEIGHT` | 0.30 | How sure Phase 7 was about what kind of market this is |
| `BREADTH_WEIGHT` | 0.20 | How many strategies expressed a direction, not just one |
| `PERSISTENCE_WEIGHT` | 0.15 | Short directional stability — deliberately the smallest |
| `BREADTH_FULL` | 3.0 | Active strategies at which breadth saturates |
| `SUPPORT_FLOOR` | 0.70 | The multiplier at zero support |
| `DISAGREEMENT_TOLERANCE` / `_CEILING` | 0.20 / 0.45 | Where the conflict penalty starts and saturates |
| `DISAGREEMENT_PENALTY` | 0.20 | Maximum support lost to conflict |
| `NOISE_TOLERANCE` / `NOISE_CEILING` | 0.55 / 0.75 | Where the noise penalty starts and saturates |
| `NOISE_PENALTY` | 0.15 | Maximum support lost to noise |
| `DEGRADED_PENALTY` | 0.10 | Support lost to degraded input |
| `MIN_SELECTION_SCORE` | 0.35 | The bar a candidate must clear to lead a board |
| `MIN_LEAD_MARGIN` | 0.05 | How far it must stand clear of the runner-up |

**These are initial heuristic ranking weights, not empirically calibrated.** Nothing here was fitted to data, because there is no outcome data. They encode only how much independent information each diagnostic is expected to add.

### Why the multiplier band is 0.70–1.00

Penalties are subtracted from *support*, not from the multiplier, so the multiplier band holds exactly by construction. Two consequences follow, and both are the point:

- A candidate with poor Phase 7 confidence can never reach the top of a board because one secondary diagnostic looked good.
- For otherwise-identical candidates, `rankScore` is strictly monotone in `ensemble.confidence` — Phase 8 may reorder near-equals, never invert Phase 7's own reading.

Penalties stay modest on purpose. Phase 7 already accounted for disagreement, noise and degraded input once; punishing them hard again here would double-count them.

### Bounds and determinism

`rankScore` is always in `0.0 … 1.0`, always finite, never NaN or infinity. Phase 7 confidence on ideal synthetic input reaches roughly 0.58, so the *practical* ceiling is near 0.6 rather than 1.0 — one more reason the number must not be read as a probability, and the reason `MIN_SELECTION_SCORE` is 0.35 rather than something that sounds decisive.

Nothing in the scoring module reads a clock. No `datetime.now()`, no `time.time()`, no age. A score is a function of the snapshot and the slot's own recent snapshots, so replaying the same data a year later produces the same board. The desktop may display "age 1.2 s" beside a board using wall-clock time; that age never reaches a stored score, and no wall-clock field is persisted.

### Directional symmetry

Nothing in the score reads which way the market is pointing. `persistence` compares directions only to each other, and no other input mentions UP or DOWN. Mirrored evidence produces identical scores, and a DOWN setup at 0.80 confidence outranks an UP setup at 0.60 under equivalent conditions.

## Stability features

Bounded per slot, and never across an identity change. History belongs to one exact `(platform, slotId, assetName, contextId)`; a new asset or context is a new series, and nothing from the previous one survives into it — not the window, and not the candidate already on the open board.

`directionPersistence3` and `directionPersistence5` are the share of the last up to three (or five) readings that agree with the current direction. Only UP and DOWN count. A NEUTRAL or SKIP occupies a place in the window — so a window that mostly abstained holds less directional evidence — but it is never counted as a vote against: a NEUTRAL is not a DOWN merely because the current reading is UP. `UP UP UP` is 1.0; `UP DOWN UP` is ≈0.667; `UP NEUTRAL UP` is 1.0.

The current snapshot is part of its own window, so a signal appearing for the first time starts at 1.0. A breakout is legitimately new, and "not present three bars ago" must never be a reason to reject a valid setup today. That is also why the persistence weight is the smallest of the four.

`confidenceMedian3` and `confidenceMedian5` are medians, not means, of the recent readings' confidence — so one extreme spike cannot move a stability diagnostic. `0.20, 0.90, 0.40` reports `0.40`.

## Candidate status

| Status | Meaning |
| --- | --- |
| `ACTIONABLE` | Phase 7 named UP or DOWN and the candidate cleared `MIN_SELECTION_SCORE` — strong enough to stand as this board's leading analysis. |
| `WATCH` | A valid directional read that did not clear the bar. |
| `NEUTRAL` | Phase 7 itself reported NEUTRAL. Preserved as NEUTRAL, never converted into a direction. |
| `EXCLUDED` | Phase 7 reported SKIP, or a hard ranking veto applies. |

"Actionable" means analytically rankable. It does not mean execute an order.

A Phase 7 SKIP is never resurrected and a Phase 7 NEUTRAL is never turned into UP or DOWN. Exclusion codes are `UNSUPPORTED_VERSION`, `INVALID_IDENTITY`, `CHRONOLOGY_INVALID`, `INVALID_ANALYSIS`, `STRATEGY_SKIP` and `STALE_FOR_EPOCH`; the first three are integrity failures, and a cohort made entirely of those is `INVALID`.

DEGRADED input may still rank, because DEGRADED is the honest steady state of live broker capture and Phase 7 has already discounted it. It is never hidden: `analysisStatus`, `qualityFit`, `noiseScore` and `disagreement` are all on the candidate, and a `DEGRADED_INPUT` reason code appears on it, so a board can never present a #1 candidate without disclosing that it is reading degraded input. If quality were catastrophic Phase 7 would already have skipped it, and Phase 8 respects that.

There is no asset favouritism. No pair, class, forex, crypto or OTC bonus or penalty exists. `EUR/USD` and `EUR/USD OTC` remain distinct identities with no special treatment either way; asset-specific calibration belongs to a phase that has outcome data.

## Ranking and tie-break

Directional candidates are sorted by:

1. `rankScore` descending
2. `ensembleConfidence` descending
3. `agreement` descending
4. `activeStrategies` descending
5. `slotId` ascending

`slotId` is unique within a board, so the order is total: every replay produces identical ranks, and nothing depends on dictionary iteration order. Feeding one complete epoch as `1,2,…,9` and as `9,5,1,8,2,7,3,6,4` produces byte-identical final boards; only the intermediate `COLLECTING` states differ.

Since ACTIONABLE is purely `rankScore ≥ MIN_SELECTION_SCORE`, sorting by score already places every ACTIONABLE candidate above every WATCH one.

## Top selection

The board may name a top analytical candidate. It is not a trade instruction. Three conservative conditions each can only *withhold* a selection:

- **The cohort must be closed** — complete, or superseded by a newer epoch. A `COLLECTING` board ranks its candidates but names no leader, because a stronger market may still arrive.
- **The leader must clear `MIN_SELECTION_SCORE` on its own.** A single ACTIONABLE market is selected only if it independently clears the bar; standards are never lowered because there is only one candidate, and a winner is never forced simply because nine slots exist.
- **The leader must stand `MIN_LEAD_MARGIN` clear of the runner-up.** At 0.610 against 0.605 the two are not distinguishable by an uncalibrated heuristic, so no primary selection is made, `LOW_LEAD_MARGIN` is recorded on the board and on both leaders, and the status is `NO_OPPORTUNITY`. Saying anything else would be false certainty.

When there is no runner-up at all, `leadMargin` is `None` and the leader carries `SOLE_CANDIDATE`.

`watchlist` is the top three ranked candidates — fewer if fewer exist, never padded.

## Reset and isolation

The reset chain runs Phase 5 → 6 → 7 → 8. When a slot's asset or context changes, `MarketEngine.reset_slots` clears the builder, the features, the strategy history and the ranking state together. Phase 8 drops the slot's stability window, its candidate on the open board, and its place in the expected cohort until it reports again under its new identity. No ranking from a previous asset survives.

An identity change also resets on ingest: an ensemble whose `(assetName, contextId)` differs from the slot's stored history rebuilds that history from scratch, so a live asset switch cannot inherit persistence or a confidence median from the market before it.

## Idempotency and ordering

One ensemble's identity is `(platform, slotId, assetName, contextId, asOf, strategyVersion)`. Receiving the same one twice increments a duplicate counter and changes nothing else: no second candidate, no distorted persistence, no second board. Phase 7 already collapses a repeated primary close, so in the live path Phase 8 rarely sees one at all.

An ensemble older than what a slot has already ranked is rejected as out of order and counted. It does not enter the stability window, it does not touch the current board, and it never rewrites a finalized one.

## Bounded state

Five recent observations per slot — exactly what the longest stability window needs — and 32 boards per platform. Nothing here grows without limit.

## Storage

Two Parquet categories, alongside the existing ones:

- `opportunity_candidates` — one row per slot per finalized epoch, carrying its final rank, status, score and every diagnostic behind it.
- `opportunity_boards` — one row per finalized epoch: what the application considered strongest at that moment, and why.

Every record carries `featureVersion`, `regimeVersion`, `strategyVersion` and `rankingVersion`. Candidates are partitioned by their own asset; a board ranks a whole cohort and no single asset owns it, so boards are filed under the reserved `asset=_cross-asset` partition — real asset partitions always carry a hash suffix, so the two can never collide.

No wall-clock value is stored as a ranking input. No render age, no `Date.now`, no poll timestamp: only market event times, so a stored board replays exactly.

## API

Local-only and read-only. Browser origins get 403; a busy engine gets 429 rather than letting a caller walk a half-updated cohort.

```
GET /api/opportunities/state                     both platform boards, kept separate
GET /api/opportunities/{platform}                the latest board (404 when none exists yet)
GET /api/opportunities/{platform}/history?limit= bounded 1..100, newest first
```

There is no POST, PUT, PATCH or DELETE. There is no endpoint that places, sizes, cancels or schedules anything. Ranking is driven by a Phase 7 event, never by a UI poll: reading every endpoint repeatedly leaves every counter and the live board exactly where they were, and a test asserts it.

## Desktop

A compact diagnostics block per workspace shows the board status, the cohort count, any missing slots, the top three ranked candidates and the lead margin — or plainly says that the top score is below the selection threshold, that no clear leader exists, or that the cohort is still collecting.

The wording is descriptive throughout: "Top analysis", never "BUY NOW", "ENTER", "BET" or "PLACE TRADE". A test asserts that no rendered label contains an instruction verb. The desktop computes no ranking of its own and has no command channel into this layer.

## What belongs to later phases

Phase 9 records paper outcomes. Phase 10 does analytics and calibration. Phase 11 does replay and backtesting. Only those phases can establish whether a `rankScore` of 0.55 actually performs better than one of 0.40. Phase 8 makes no such claim, and must not be read as making one.
