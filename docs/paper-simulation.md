# Paper outcome simulation (Phase 9)

`qst-paper-v1`

Phase 6 produces facts, Phase 7 interprets one market, Phase 8 compares those opinions across
markets. None of them can answer the only question that makes any of it checkable:

> If Phase 8 selected this opportunity at that time, what would have happened if a
> hypothetical trade had been entered?

Phase 9 answers it, and nothing else. It is a measurement layer — not another strategy, and not
an execution layer.

```
Phase 8 board (READY, one selected candidate)
        ↓
paper entry intent                     PENDING_ENTRY
        ↓   first canonical price at or after decisionAvailableAt
canonical entry price                  OPEN
        ↓   + 5 s (CapitalBear) / 60 s (IQ Option) of market time
canonical expiry price                 RESOLVED
        ↓
WIN / LOSS / DRAW / INVALID
        ↓
simulated P/L, only if an operator configured a stake and payout rate
        ↓
PaperTrade + PaperTradeEvent persisted, PaperSettlement emitted once
```

## Two unrelated things called "paper"

This project now has two, and conflating them would make both meaningless.

| | Desktop `ExecutionManager` PAPER mode | Phase 9 `PaperEngine` |
| --- | --- | --- |
| Lives in | `apps/desktop/electron/main/execution-manager.ts` | `services/quant-engine/src/quant_engine/paper/` |
| Asks | Would this board have produced a press? | What did the market do after this board? |
| Reads | Broker control map, order panel | Canonical Phase 5 price samples |
| Produces | An `OrderTicket` in state `PAPER` | A `PaperTrade` with an outcome |
| CONFIRMED means | The broker panel visibly reacted to a press | *nothing — it is not a paper vocabulary* |
| WIN means | *nothing — it is not an execution vocabulary* | The market moved the way the analysis said |

An execution ticket saying CONFIRMED and a paper trade saying WIN are different claims about
different things. The desktop renders them in separate panels with disjoint wording, and a test
asserts the two label vocabularies never overlap.

Phase 9 imports no execution module, binds no execution name, and has no broker actuator of any
kind. `test_paper_safety.py` scans every name the package binds, reads and calls, and fails on
any of `sendInputEvent`, `WebContentsView`, `ExecutionManager`, `OrderExecutor`, `pressPoint`,
`press`, `click`, `arm`, `disarm`, `higher`, `lower`, `buy`, `sell`, `wallet`, `balance`,
`bankroll`, `martingale` and the rest. `UP` and `DOWN` are allowed: they describe what the
market did, not a button on a broker panel.

## Version contract

Every persisted `PaperTrade` records five versions:

| Field | Supported value |
| --- | --- |
| `featureVersion` | `qfe-v2` |
| `regimeVersion` | `qst-regime-v1` |
| `strategyVersion` | `qst-strategy-v1` |
| `rankingVersion` | `qst-ranking-v1` |
| `paperVersion` | `qst-paper-v1` |

A board carrying any other upstream version is refused with `UNSUPPORTED_VERSION` and is never
simulated. This is deliberate rather than cautious: a paper outcome is evidence about what a
*particular* set of upstream contracts produced, and pooling `qst-ranking-v2` decisions with
`qst-ranking-v1` outcomes would make both statistics meaningless. The supported strings are
literals in `paper/models.py`, not imports of the upstream constants, so a later contract
arrives as an explicit refusal rather than a silent re-interpretation.

`PAPER_VERSION` covers the entry policy, the expiry horizons, the timeout bounds, the outcome
rules and the payout semantics. Changing any of them requires a new string.

## Board eligibility

A paper intent is created only when **all** of the following hold:

- `board.status == READY`
- `board.selectedSlotId is not None`
- `board.selectedDirection in {UP, DOWN}`
- every upstream version is supported
- `settings.enabled`

`COLLECTING`, `PARTIAL`, `NO_OPPORTUNITY` and `INVALID` boards create nothing. A Phase 7
`NEUTRAL` or `SKIP` is never converted into a direction. **Phase 8's gates are never lowered to
obtain more paper trades** — a run that produces no eligible selection reports that, and the
unit and replay tests are what prove the layer works.

`requireReadyBoard = False` additionally accepts a `PARTIAL` board that named a leader. It is
not the default, and a `PARTIAL` outcome is tagged on the trade through `boardStatus` so Phase
10 can separate the two populations.

## `decisionAvailableAt` — the heart of the layer

`board.asOf` is the primary close the cohort describes. It is **not** the time the finished
decision existed.

A CapitalBear S5 bar closing at `10:00:05.000` is only closed once a sample past that boundary
arrives, and the ninth slot of the cohort is processed after that. The complete board might not
exist until `10:00:06.200`. Entering at `10:00:05.000` would price a decision at a moment nobody
could have acted on it — a backdated fill that would flatter every statistic downstream.

So every trade carries an explicit `decisionAvailableAt`, propagated from the canonical
pipeline:

1. `TimeSeriesBuilder` emits each record as an `Emission(record, availableAt)`, where
   `availableAt` is the watermark that closed it.
2. `MarketEngine.persist_events` carries that time through Phase 6 and Phase 7 into
   `rank_opportunity`.
3. Each slot's latest ensemble availability is recorded in `MarketEngine.availability`.
4. `MarketEngine.board_available_at` takes the **maximum** over the cohort's members, never
   below `board.asOf`. A cohort is exactly as available as its slowest member, and taking the
   completing arrival's watermark alone would understate it.

Taking a maximum can only move an entry later, which is the only direction a measurement layer
is allowed to be wrong in.

`decisionAvailableAt < board.asOf` is refused as `CHRONOLOGY_INVALID`.

### Determinism

The paper layer never calls `datetime.now()`, `time.time()` or any clock. A test asserts the
package imports neither `time` nor `datetime` and binds none of `now`, `utcnow`, `monotonic`,
`perf_counter`. Its only notion of time is the canonical market event times the pipeline shows
it.

In replay and in tests the availability watermark is always a canonical sample's own timestamp,
so identical recorded events reproduce identical trade ids, decision times, entries, expiries,
outcomes and P/L. In live capture the watermark may come from Phase 5's own three-second
availability clock (`advance_live`) — that is Phase 5's existing behaviour, not something Phase
9 introduces, and `advance_live` only touches builders whose last sample came from `DOM` or
`VISUAL`.

### Event ordering

`MarketEngine.ingest` offers each accepted sample to the paper layer **after** the whole Phase
5-8 chain for that sample has run. So a sample that closes the bar that completes a cohort has
already created the paper intent by the time it is offered as a price, and it is honestly the
first canonical price at or after the decision became available. Ordering is therefore causal
rather than scheduling-dependent: CPU scheduling cannot move an entry earlier or later.

Only accepted samples are offered. The per-second record carries the same timestamp and price,
and replaying it would re-offer a price the layer has already seen.

## Entry policy

A `PENDING_ENTRY` trade takes the **first** canonical sample that

- belongs to the same platform, slot, asset and context,
- passes the canonical quality bar (`GOOD` or `DEGRADED`, finite, positive), and
- has `sampleTime >= decisionAvailableAt`.

There is no search backwards for a better fill, no use of the pre-signal price, no reaching into
a candle's low or high, and no backdating to `board.asOf`. An unusable sample is skipped, never
coerced: there is no fallback price, no last-known value, and no zero.

Once taken: `status = OPEN`, `entryTime`/`entryPrice`/`entrySource`/`entryQuality` recorded, and
`expiryTargetTime = entryTime + PAPER_DURATION_MS[platform]`.

### Entry timeout

Measured from `decisionAvailableAt`:

| Platform | `MAX_ENTRY_DELAY_MS` |
| --- | --- |
| CapitalBear | 5 000 |
| IQ Option | 10 000 |

Past it, the trade becomes `INVALID` / `INVALID` with `ENTRY_TIMEOUT`. No entry is invented. One
horizon on CapitalBear: a decision that could not be entered inside the bar it was made for
describes a market that has already moved on.

## Expiry policy

| Platform | `PAPER_DURATION_MS` | `MAX_RESOLUTION_LAG_MS` |
| --- | --- | --- |
| CapitalBear | 5 000 | 5 000 |
| IQ Option | 60 000 | 10 000 |

The expiry price is the **first** eligible sample with `sampleTime >= expiryTargetTime`. A
sample a millisecond before the target never settles the trade however favourable it looks, and
once a qualifying sample is taken the trade is resolved, so a later and better price cannot
replace it.

If nothing eligible arrives by `expiryTargetTime + MAX_RESOLUTION_LAG_MS`, the trade becomes
`INVALID` with `RESOLUTION_TIMEOUT`. A price taken long after the horizon is a different market.

The horizons are simulation parameters chosen to match each broker's own shortest contract. They
are not read from a broker, and nothing here schedules a real expiry.

## Outcome logic

| Direction | Expiry vs entry | Outcome |
| --- | --- | --- |
| UP | `expiry > entry` | WIN |
| UP | `expiry < entry` | LOSS |
| DOWN | `expiry < entry` | WIN |
| DOWN | `expiry > entry` | LOSS |
| either | `expiry == entry` | DRAW |

Strict comparison, **no tolerance band**. The project has no canonical tick-size contract, so any
band would be an invented number that quietly reclassified real losses as draws.

`INVALID` means the canonical data needed to enter or resolve never arrived in time, and
`CANCELLED` means the identity the trade belonged to stopped existing. Neither is scored as a
loss.

`priceDelta` and `priceDeltaBps = (delta / entryPrice) × 10 000` are recorded as movement
diagnostics. They are descriptive; they are not profit and are not payout-adjusted.

## Identity, context and reset

A paper trade belongs to exactly one `(platform, slotId, assetName, contextId)`. A sample on the
same slot carrying a different asset or context cancels the trade — `ASSET_CHANGED` or
`CONTEXT_CHANGED` — whether it is pending or open. The physical slot is reused when an operator
changes an asset, so matching on the slot alone would let a later BTC price settle an EUR/USD
trade.

The reset chain now runs Phase 5 → 6 → 7 → 8 → **9**. `MarketEngine.reset_slots` cancels live
paper trades on the reset slots with `SLOT_RESET`. Resolved history is never deleted: it is
evidence about a market that really moved that way.

### Source mode isolation

`DOM` and `VISUAL` are the same real broker screen — the capture layer falls back between them
mid-series on purpose — so both may appear inside one live trade. `REPLAY` and `SYNTHETIC`
describe different worlds and never mix with them or with each other. A trade entered from a
`REPLAY` sample cannot be resolved by a `DOM` sample; it waits, and times out honestly.

## Concurrency

- At most **one** live paper trade per `(platform, slot)`. A new selection on a slot that
  already has one is refused with `SKIPPED_ALREADY_OPEN`.
- At most `MAX_OPEN_PER_PLATFORM = 3` live trades per platform, refused with
  `SKIPPED_PLATFORM_LIMIT`. This is simulation-state protection, not bankroll sizing, and it is
  not tuned.
- CapitalBear slot 3 and IQ Option slot 3 are entirely independent, with different horizons.

## One selection, one trade

`paperTradeId` is a UUID5 over `(platform, boardAsOf, slotId, assetName, contextId,
rankingVersion, paperVersion)`. The same board replayed under the same versions produces the same
id, so a replay reproduces history rather than appending a parallel copy.

A decision is recorded per `(platform, boardAsOf)` the moment it is *made*, whatever is decided.
A board reaches the paper layer twice — once when its cohort completes, and again when the next
epoch makes it immutable — and the second arrival carries a later watermark. Recording the
decision at the first arrival means a selection refused for concurrency stays refused, rather
than being entered seconds late at a restated decision time it never had. Repeat deliveries
increment `duplicateSelections`.

## Paper accounting

`paperPayoutRate` is the **net profit fraction on a win**, matching how binary brokers quote it.

| Outcome | `realizedPaperPnl` |
| --- | --- |
| WIN | `paperStake × paperPayoutRate` |
| LOSS | `−paperStake` |
| DRAW | `0` |
| INVALID / CANCELLED / unresolved | `None` |

With `stake = 50` and `payoutRate = 0.82`: a win is `+41`, a loss is `−50`, a draw is `0`.

**There is no default stake or payout rate.** A directional outcome is always knowable from
canonical prices; a monetary result is not knowable without an explicit stake and payout model.
With none configured the layer still resolves WIN / LOSS / DRAW and reports `realizedPaperPnl`
as `None`. `None` means "not known" and is never collapsed to `0.0`, which would read as
break-even.

Accounting settings must be complete or absent: a half-configured block is refused outright.
Phase 9 never reads or infers a payout percentage from a broker panel — real payout tracking is
a separate problem.

### Snapshot at entry

`paperCurrency`, `paperStake` and `paperPayoutRate` are copied onto the trade **when it opens**.
An operator who changes the simulated payout rate halfway through must not retroactively rewrite
what an already-running simulation was measuring: a trade opened at `0.80` resolves at `0.80`
even if the settings later say `0.90`.

### Configuring it

Through the process environment, the project's existing trusted configuration channel. There is
deliberately no HTTP endpoint that writes these.

```
QST_PAPER_ENABLED=true
QST_PAPER_REQUIRE_READY_BOARD=true
QST_PAPER_CURRENCY=THB
QST_PAPER_STAKE=50
QST_PAPER_PAYOUT_RATE=0.82
```

The horizons and timeout bounds are **not** environment-tunable: they are part of the version
contract, and changing them would silently redefine `qst-paper-v1`. A malformed accounting block
is reported on `/api/paper/state` as `settingsError` and the engine starts with accounting off,
because refusing to record directional outcomes over a typo would lose real evidence and protect
nothing.

## Storage

Two new Parquet categories under the existing market-data root:

- `paper_trades` — one append-only row per lifecycle transition, partitioned by platform, asset
  and the decision's date, so every row of one trade lands in one partition however long the
  horizon ran.
- `paper_trade_events` — `PENDING_CREATED`, `OPENED`, `RESOLVED`, `CANCELLED`, `INVALIDATED`,
  each with its market-time `eventTime` and its reason.

Every resolved row retains the selection metadata Phase 10 will need to ask *which* decisions
won: platform, asset, slot, `boardAsOf`, `decisionAvailableAt`, direction, rank, `rankScore`,
`ensembleConfidence`, `agreement`, `primaryRegime`, `regimeConfidence`, `boardStatus`,
`leadMargin`, both prices, the outcome, the simulated P/L and all five versions.

## Restart

On start-up `MarketEngine.restore_paper` reloads `paper_trades`, keeps the newest state per
`paperTradeId` (terminal states are absorbing, so status precedence orders the transitions), and
hands them to `PaperEngine.restore`.

- `RESOLVED`, `CANCELLED` and `INVALID` come back as history, so statistics survive a restart.
  A resolved trade never re-emits its settlement.
- `PENDING_ENTRY` and `OPEN` **cannot** be continued and are cancelled with
  `RESTART_UNRESOLVABLE`, and that cancellation is itself persisted. The canonical series they
  were waiting on is gone, and the capture layer mints a new `contextId` for every slot when it
  starts, so the exact identity they need provably cannot return.

Nothing disappears silently. An unreadable paper history never stops the engine from starting —
live capture is the thing that cannot be recovered later.

## API

Local-only and read-only, behind the same trust boundary as the rest of the engine: a request
carrying an `Origin` or `Sec-Fetch-Site` header is refused with 403, and a request arriving while
the ingestion thread is mutating shared state gets 429 rather than a half-updated read.

| Endpoint | Returns |
| --- | --- |
| `GET /api/paper/state` | Version contract, counters, per-platform tallies |
| `GET /api/paper/open` | Every pending and open trade |
| `GET /api/paper/history?platform=&limit=` | Finished trades, newest first, `limit` 1-500, default 100 |
| `GET /api/paper/trades/{paperTradeId}` | One trade by its deterministic id |
| `GET /api/paper/stats?platform=` | Descriptive statistics |
| `GET /api/paper/settlements?limit=` | The Phase 9.5 hand-off, newest first |
| `GET /api/paper/settings` | Effective settings and the frozen policy constants |

There is no `POST`, `PUT`, `PATCH` or `DELETE` anywhere in the surface, and a test asserts it.

## Phase 9.5 interface

Each resolved trade emits exactly one `PaperSettlement`:

```
tradeId, platform, assetName, settledAt, outcome (WIN|LOSS|DRAW),
currency, stake, payoutRate, realizedPnl, paperVersion
```

`currency`, `stake`, `payoutRate` and `realizedPnl` are `None` when accounting is not
configured. A settlement is emitted once and never re-emitted — including across a restart —
because a daily session guard that counted one twice would act on a fabricated result.

**The daily profit target and loss guard are Phase 9.5 and are not implemented.**

## Statistics are descriptive

Per platform and overall: `resolved`, `wins`, `losses`, `draws`, `invalid`, `cancelled`,
`winRateExcludingDraws`, `winRateIncludingDraws`, current and maximum win/loss streaks,
`averagePriceDeltaBps`, and — only when at least one trade carried an accounting snapshot —
`grossPaperProfit`, `grossPaperLoss`, `netPaperPnl`. Any non-win ends a win streak; a draw is not
a win.

These describe what was recorded. They are not a claim about what will happen, no accuracy or
profitability is asserted anywhere, and **no Phase 6, 7 or 8 threshold may be changed on the
strength of them**. Phase 9 records evidence; Phase 10 will analyse it.

## Desktop diagnostics

The trading window gains a `ผลจำลอง (Paper)` panel above the execution controls: the live trades
with their entry price and expiry target, the recent results with their movement in basis points
and simulated P/L, and the tally. With no accounting configured the panel says
`ยังไม่ได้ตั้งค่าเงินจำลอง` rather than showing a zero.

The panel reads one bridge method, and that method is a read. It contains no broker control, no
arm or disarm, and no path to the execution layer.

## Performance

`node scripts/python.mjs services/quant-engine/tests/benchmark_paper.py` drives 1 200 boards and
471 600 canonical samples across both platforms and three slots each.

Measured: **131 273 samples per second**, 10 267 boards per second, 2.79 MB peak traced Python
memory, history bounded at its 512-row cap and at most six live trades. Paper work accounted for
36% of the benchmark's own wall time — the rest is building the Phase 8 boards it consumes — and
is negligible beside OCR and feature computation. Live, eighteen slots produce a few canonical
samples per second.

## Tests

`test_paper_outcome.py` (the five outcomes, symmetry, movement diagnostics, accounting and the
entry snapshot), `test_paper_lifecycle.py` (no-lookahead on entry and expiry, both timeouts,
context and asset changes, source-mode isolation, duplicates, deterministic ids, concurrency
caps, platform separation, horizons, reset, replay determinism), `test_paper_eligibility.py`
(board statuses, abstentions, the version contract, Phase 10 metadata), `test_paper_pipeline.py`
(the synthetic acceptance case end to end through the real Phase 5-8 chain, with a deliberately
lagging second slot so `decisionAvailableAt > board.asOf` on every trade),
`test_paper_storage.py` (Parquet round trip, transition history, restart),
`test_paper_api.py` (local-only, busy-safe, bounded history) and `test_paper_safety.py` (no
recomputation of Phase 6-8, no clock, no execution name, no broker-reachable import).
