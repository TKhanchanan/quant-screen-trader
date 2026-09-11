# Daily session guard (Phase 9.5)

`qst-session-guard-v1`

Phase 9 answers *what did the market do after that decision?* This layer answers the only
question that follows from a day of those answers:

> How much have we actually gained or lost today, and is the session still allowed to accept
> new entries?

```
Phase 8 board
     ├── existing execution path ──────────────► broker press (unchanged)
     │                                                  ▲
     └── Phase 9 paper outcome                          │ additional veto
              ↓                                         │
         PaperSettlement                                │
              ↓                                         │
     ┌────────────────────────┐                         │
     │ Phase 9.5 SessionGuard │                         │
     │ realized daily P/L     │                         │
     │ profit target / loss   │                         │
     └────────────────────────┘                         │
              ↓                                         │
     canOpenNewEntry: true / false ──────────────────────┘
```

## It is a stop system

The one rule the whole layer exists to protect:

| Wrong | Right |
| --- | --- |
| "We need ฿100 more today, lower the rank score threshold." | The quant layers produce exactly the decisions they would have produced anyway. |
| "We lost twice, increase the stake." | There is no stake anywhere in this layer. |
| "The target is close, allow PARTIAL boards." | Phase 8's gates are not reachable from here. |

The guard can only ever **withdraw** permission. It never creates an entry, never names a
direction, slot, asset or score, and holds no position size of any kind. `test_session_guard_safety.py`
scans every name the package binds and fails on `martingale`, `stakeMultiplier`, `lossRecovery`,
`doubleAfterLoss`, `chaseLoss`, `increaseStake`, `positionSize`, `compounding` — and equally on
`rankScore`, `confidence`, `threshold`, `direction`, `slotId`, `strategy` and `regime`, because a
stop system that starts forming opinions about the market is no longer a stop system. It imports
no feature, regime, strategy or ranking module, and the only thing it takes from Phase 9 is
`paper.models`.

A losing run changes exactly one thing: whether the day is still open.

## Realized money only

Stop decisions use `realizedPnl` — outcomes that have actually settled. Never an open-trade
estimate, an unrealized broker P/L, a predicted outcome, an expected value or a mark-to-market
number.

> Realized +580, one open paper trade that might win +40.
> The session is **+580**, not +620, until that trade settles.

Stopping a day on money that has not happened yet would be stopping on a prediction.

## Accounting source

`accountingSource` is `PAPER` in this version, and it is persisted on every session so stored
history can never be mistaken for real money later. The guard consumes Phase 9's
`PaperSettlement` and nothing else; a `paperVersion` other than `qst-paper-v1` is refused rather
than pooled with totals it does not share semantics with. **No broker balance is read or scraped
anywhere in this phase.**

### What a settlement may contribute

| Situation | Win/loss tally | Daily P/L | Code |
| --- | --- | --- | --- |
| Money, right currency, first delivery | yes | yes | — |
| `realizedPnl` is `None` | yes | no | `NO_MONETARY_VALUE` |
| Different currency from the session's | yes | no | `CURRENCY_MISMATCH` |
| Same `tradeId` already accounted | no | no | `DUPLICATE` |
| Settled in a day already closed | no | no | `LATE_SETTLEMENT` |
| `paperVersion` not supported | no | no | `UNSUPPORTED_PAPER_VERSION` |
| Not a finite number | no | no | `NOT_FINITE` → fails the session closed |

A WIN alone is never enough to move the money. Phase 9 reports money only when an operator
configured a simulated stake, payout rate and currency; where it did not, the outcome is real
and the money is unknown — and unknown is never zero.

**No hidden FX.** THB and USD are never added. A rate nobody configured is a number nobody can
audit, so a foreign settlement is named and excluded.

## Profit target

With the guard enabled and a target set, `realizedPnl >= target` transitions `ACTIVE →
TARGET_REACHED`, sets `canOpenNewEntry = false` and `stopReason = DAILY_PROFIT_TARGET`, and
records `targetReachedAt` and `tradesToTarget`.

Crossing, not equality: 575 plus a 41 win is 616, and a 600 target has been reached. Requiring an
exact landing would mean it almost never fires.

## Daily loss limit

The limit is a positive magnitude compared against a negative total: `realizedPnl <= -limit`.
−280 plus another −50 is −330, and a limit of 300 has been passed.

## A stop wins over everything

The moment a limit is reached, `canOpenNewEntry` is `false`. It does not wait for the next
Phase 8 board, the next tick or the next poll.

## Outcomes that settle after the stop

A trade that was already running when the day stopped is allowed to finish, and its result
changes the **final P/L** and nothing else.

> Target 600, tripped at +610. A remaining trade later loses 50. Final: **+560**.
> The session stays stopped. It does not un-trigger.

> Loss limit 300, tripped at −310. A remaining trade later wins 80. Final: **−230**.
> The session stays stopped.

There is no automatic resume anywhere in this layer, and `tradesToTarget` is frozen at the
trigger: it is about how many trades it took to get there, not about what happened afterwards.

## Open trades and completion

A stop is staged rather than instantaneous:

1. new entries are blocked and `stopReason` is set;
2. the session and its event are handed to the caller to persist;
3. the notification is raised;
4. if `waitForOpenTradesBeforeClose` (the default) and Phase 9 still has unresolved trades, the
   status becomes `WAITING_FOR_SETTLEMENT` — already stopped, not yet finished;
5. when the last outcome settles, `COMPLETED`;
6. and then `LOCKED_FOR_DAY` if the matching lock flag is set.

Finishing all of that inside the call that trips the limit would mean a target could be reached,
completed, locked and the application asked to quit before anything had been written down.

The unresolved count comes from `PaperEngine.unresolved()`, never from execution tickets: a
ticket describes a press, a paper trade describes a measurement that is still running.

## The daily lock

After a target or a loss limit completes, the day becomes `LOCKED_FOR_DAY` and there is no
re-arm for that trading date. Reopening the application at 12:00 after a target at 10:00 comes
back `LOCKED_FOR_DAY` with `canOpenNewEntry = false` — never as a fresh session.

**Turning the guard off does not lift a lock.** Disabling prevents future stops; it does not
undo one that already happened. A loss limit an operator can lift by unticking a box is not a
loss limit.

## Trading date, timezone and reset hour

A session belongs to a *local trading date*, derived with `zoneinfo` from the configured
timezone — never from the UTC date and never from the machine's locale. The default is
`Asia/Bangkok`.

| Boundary | Belongs to |
| --- | --- |
| 23:59:59.900 Bangkok | that day |
| 00:00:00.100 Bangkok | the next day |
| `resetHour = 5`, 04:59 | the previous trading date |
| `resetHour = 5`, 05:00 | the new trading date |

**A settlement belongs to the day it settled in, not the day it was entered.** A trade entered
at 23:59 that settles three seconds after midnight is the new day's money, because that is when
its money existed.

At the next boundary a new session is created with zero P/L and zero resolved trades, and the
previous one is retained in history. Yesterday is never overwritten.

`sessionId` is a UUID5 over profile, trading date, timezone, currency and guard version, so the
same day always reloads as the same session.

## Duplicate protection

A `tradeId` contributes exactly once. Processed ids live in the persisted event log, so a
settlement accounted before a restart is recognised after one and counted as `DUPLICATE`. This is
the single most important property in the layer: a day that doubles its own profit every time the
application reopens would stop on money that was only ever counted twice.

## Restart recovery

On start-up the guard loads the persisted sessions and events and **replays today from its event
log** rather than trusting its snapshot.

A snapshot and an append-only log live in separate files and can disagree after a crash. Trusting
the snapshot could double-count a settlement whose id never reached the log; replaying the log
cannot, because the same record carries both the money and the identity that makes it idempotent.
Snapshots remain the record for finished days, which no longer change.

Restored: realized P/L, gross profit and loss, wins/losses/draws, processed trade ids, status,
locks, the target and loss timestamps, and the notification flags. Nothing resets to zero.

**Nothing reconstructed notifies.** A target reached and announced before the restart does not
announce itself again.

## Manual stop

`stop_session` transitions to `STOPPED_MANUALLY` and blocks new entries immediately. Nothing may
stand between an operator and it: no target, no accounting, no settlement and no session yet are
required, and the HTTP endpoint deliberately does not return 429 while the engine is busy — it
waits briefly for a clean moment and then proceeds. A risk control an operator cannot reach when
the engine is mid-batch is not a risk control.

A manual stop completes without locking the date, and never requests a shutdown.

There is no automatic resume after any stop, and no hidden resume behaviour anywhere.

## Accounting errors fail closed

A non-finite settlement value sets `ACCOUNTING_ERROR`, blocks new entries and records the reason.
Continuing on a number the layer knows is wrong would be worse than stopping on one it knows is
right.

## The permission contract

| Status | `canOpenNewEntry` | `blockReason` |
| --- | --- | --- |
| `ACTIVE` | true | — |
| `DISABLED` | true | `GUARD_DISABLED` |
| `TARGET_REACHED` | false | `DAILY_PROFIT_TARGET` |
| `LOSS_LIMIT_REACHED` | false | `DAILY_LOSS_LIMIT` |
| `WAITING_FOR_SETTLEMENT` | false | the stop reason |
| `STOPPED_MANUALLY` / `COMPLETED` | false | `MANUAL_STOP` or the stop reason |
| `LOCKED_FOR_DAY` | false | `LOCKED_FOR_DAY` |
| `ACCOUNTING_ERROR` | false | `ACCOUNTING_ERROR` |

`GUARD_DISABLED` is never a block. It is reported so a reader can tell *the guard permits this*
from *the guard is not watching*, which are different facts.

### How the execution layer uses it

`ExecutionManager` reads the permission once per tick and, when it is `false`, adds one more
named refusal (`SESSION_DAILY_LOSS_LIMIT`, `SESSION_LOCKED_FOR_DAY`, …) to the block list it
already computes. The veto is **sticky**: a stop that has been seen stays in force until the
engine positively says otherwise, because a limit that lifts itself whenever a poll fails is not
a limit.

Nothing else in the execution layer changed. AUTO, PAPER, Arm, Disarm, the control map, the
Higher/Lower mapping, the all-controls test, the cooldown, the hourly cap, the allowed-slot list,
the confidence limits, the tab identity check, CONFIRMED/UNVERIFIED and the unverified breaker
are all exactly as they were, and a test asserts each of them still exists.

The guard does **not** gate Phase 9. Paper outcomes keep being measured for every Phase 8
selection whatever the day's P/L is — gating the measurement layer would bias the only evidence
this system produces.

## Settings

| Field | Default | Notes |
| --- | --- | --- |
| `enabled` | `false` | A risk control that switched itself on with invented numbers would be enforcing somebody else's idea of a good day. |
| `dailyProfitTarget` | `None` | Must be > 0 when present. |
| `dailyLossLimit` | `None` | Must be > 0 when present; a magnitude. |
| `currency` | `THB` | Never converted. |
| `timezone` | `Asia/Bangkok` | Must be a real IANA zone. |
| `resetHour` | `0` | 0–23. |
| `notifyOnProfitTarget` / `notifyOnLossLimit` | `true` | |
| `closeAppOnProfitTarget` / `closeAppOnLossLimit` | `true` | |
| `waitForOpenTradesBeforeClose` | `true` | |
| `lockAfterProfitTarget` / `lockAfterLossLimit` | `true` | |

**No amounts are invented.** A target of `0`, a negative limit, `NaN`, `Infinity`, an hour of `24`
or an unknown timezone are each refused with their reason — never silently corrected. Coercing any
of them would mean the guard enforcing a rule nobody asked for.

Settings live in the local SQLite database (migration `0007_session_guard.sql`) and are written
through the same local-only trust boundary as the workspace configuration endpoint.

### Changing the limits mid-day

Applied immediately, including to money already realized.

> Today is +500 with a 600 target. The operator lowers the target to 400.
> The session reaches its target at once.

> Today is −250 with a 300 limit. The operator tightens it to 200.
> The loss limit is breached at once.

The alternative — new limits counting only from the next settlement — would let an operator
tighten a limit and keep trading past it. Changing the currency, timezone or reset hour starts a
separate session rather than retrofitting the running one with terms it was never accounted under.

## Notifications

Python states that something notification-worthy happened; Electron shows it. The engine never
touches an operating system notification centre.

Each transition notifies once. Repeated polling produces nothing new, a restart never
re-announces a transition the operator was already told about, and the desktop watcher seeds its
already-seen set on the first poll so opening the application does not replay the morning.

> **Daily profit target reached** — Realized P/L +฿617.00 · target +฿600.00 · new entries are stopped.

> **Daily loss limit reached** — Realized P/L -฿315.00 · loss limit -฿300.00 · new entries are stopped.

## Graceful shutdown

Python exposes `shutdownRequested` and never terminates anything. Electron owns the application's
lifetime.

The flag is set only once **all** of these hold: new entries are blocked, the session state and
its events have been handed over for persistence, the notification has been raised, the stop was
a target or loss limit whose `closeApp…` flag is set, and — when `waitForOpenTradesBeforeClose` —
every open outcome has settled.

The desktop watcher then asks the application to quit through the same path a person closing the
window takes, so observation stops cleanly, state is flushed and the local engine is shut down in
order. Nothing is ever forced; the watcher contains no `process.exit`.

## Statistics

Per session: realized P/L, gross profit, gross loss, wins, losses, draws, invalid, resolved and
monetary trade counts, win rates excluding and including draws (`None` when the denominator is
zero), largest win, largest loss (stored negative, on the same axis as the total), peak and trough
realized P/L, maximum realized drawdown, trades to target and duration to target.

Drawdown is the largest fall from a realized peak: a path of +200, +350, +100 has a peak of 350
and a maximum realized drawdown of 250. It is descriptive, and nothing in this layer reacts to it.

None of these numbers is a claim about what the next day will do, and no Phase 6, 7 or 8 threshold
may be changed on the strength of them.

## Storage

Two Parquet categories under the existing market-data root, filed under a shared `_session`
partition because a trading day spans both platforms and every asset that settled in it:

- `daily_sessions` — one row per state change, newest `revision` wins.
- `session_guard_events` — `SESSION_CREATED`, `SETTLEMENT_APPLIED`, `SETTLEMENT_REJECTED`,
  `PROFIT_TARGET_REACHED`, `LOSS_LIMIT_REACHED`, `MANUAL_STOP`, `WAITING_FOR_SETTLEMENT`,
  `SESSION_COMPLETED`, `SESSION_LOCKED`, `SESSION_RESET`, `ACCOUNTING_ERROR`.

Event ids are deterministic over session, type and cause, so a replayed transition is the same
event and a restart cannot duplicate one.

## API

Local-only, behind the same trust boundary as the rest of the engine: an `Origin` or
`Sec-Fetch-Site` header is refused with 403, and reads return 429 rather than a half-updated
session while the ingestion thread is mutating it.

| Endpoint | Returns |
| --- | --- |
| `GET /api/session-guard/state` | Permission, session, progress, notifications, shutdown flag |
| `GET /api/session-guard/settings` | Effective settings and any error reading them |
| `GET /api/session-guard/history?limit=` | Day summaries, newest first, `limit` 1–365, default 30 |
| `GET /api/session-guard/sessions/{sessionId}` | One day and its summary |
| `GET /api/session-guard/events?limit=` | Recent transitions |
| `POST /api/session-guard/command` | `settings` (refused while busy) or `stop` (never refused) |

There is exactly one write endpoint and no remotely reachable way to lift a limit or restart a
locked day.

## Desktop

The trading window gains a **รอบวัน (Daily Session)** panel: status, realized P/L, target and what
remains, separate profit and loss progress bars, the W/L/D tally, the number of unresolved
outcomes, the next reset when the day is locked, the settings form, and a **Stop Session** control.

Stop Session and Disarm are deliberately different controls with different words. Stopping ends
the trading day and cannot be undone before the next reset; disarming only takes the executor's
finger off the button.

A day with no configured simulated money shows `ยังไม่ได้ตั้งค่าเงินจำลอง` rather than ฿0, which
would read as break-even on a day nobody priced.

## Performance

`node scripts/python.mjs services/quant-engine/tests/benchmark_session_guard.py` drives 10,000
settlements across 40 trading days.

Measured: **15,462 settlements per second**, 1.02 MB peak traced Python memory, history bounded at
its 365-day cap, the event log at 512 rows and the processed-id set at one day's worth. The guard
adds one addition and two comparisons per settled outcome and is negligible beside OCR and feature
computation.

## Tests

`test_session_guard_accounting.py` (target and loss crossings, draws, unpriced outcomes, currency
isolation, non-finite values, the descriptive statistics and drawdown),
`test_session_guard_lifecycle.py` (duplicates across restarts, stopping with outcomes still
running, no resume when the final P/L crosses back, restart, day boundaries and reset hours,
manual stop, notification idempotency, shutdown staging, the permission contract),
`test_session_guard_settings.py` (validation, the disabled guard, mid-day limit changes),
`test_session_guard_storage.py` (Parquet round trip, history, replay determinism, and the real
Phase 5–9 chain feeding a daily total through `MarketEngine`), `test_session_guard_api.py`
(local-only, busy-safe, bounded, and a stop that succeeds while busy) and
`test_session_guard_safety.py` (no recovery or escalation name, no quant import, no clock, no
broker-reachable import). `apps/desktop/tests/session-guard.test.ts` covers the bridge, the
labels, the panel and the execution regression.
