# Execution

The execution layer turns a finished opportunity board into a press on the broker's own entry
control. It is the first layer in this application that acts rather than observes, and everything
in it is built around one rule: a coordinate that will be clicked must be re-proved, not remembered.

## What it presses, and what it does not set

The layer presses **one direction control in one chart cell**. That is all it does.

It does **not** set the stake, the expiry, the account, or anything else on the broker panel. Those
stay exactly as you left them in the broker's own interface, and the press inherits whatever is
already selected there. Before arming, set the stake and expiry yourself and confirm they are what
you intend for **every** cell the executor is allowed to use — a press on slot 7 uses slot 7's panel
settings, not slot 3's.

The layer also reads no balance, holds no position record, and computes no profit or loss. An order
ticket records that a control was pressed, never that the trade won.

## The pipeline

```text
Phase 8 board (one platform, one close)
  └── operator limits: board status, rank score, ensemble confidence, allowed slots
      └── one ticket per board close, deduplicated by the board's own close time
          └── tab identity re-proved for the named slot
              └── control map lookup for that canvas cell
                  └── press → capture the panel again → CONFIRMED or UNVERIFIED
```

The engine's own selection gate runs first and is not re-implemented here. If the board names no
leader, there is nothing to press. The operator limits sit on top of that gate and can only make it
stricter.

## Setting it up

Order matters. Each step depends on the one before it.

1. Log in manually inside the workspace browser and open all nine charts.
2. **Sync Assets**, then **Calibrate Chart Area**, then **Start observation**. The executor requires
   a verified grid; without one it stays blocked.
3. Set the stake and expiry on the broker's own panels, in every cell you intend to trade.
4. **Measure order controls**. This captures the surface once and locates the two colored controls
   in each cell's reserved panel strip. The panel reports how many of the nine cells were measured
   and names the ones that were not.
5. Set **Green button means** to whatever the broker's own labels say. The application never assumes
   that green is Higher — check the label and set it.
6. Set the limits, choose **AUTO**, then **Arm**.

**STOP** disarms immediately and is never blocked by anything else on the panel.

## Testing the controls

**Test all 18 controls** presses every measured control once, both directions, cell by cell with a
short gap. It ignores the board, the score limits and the rate limits, because the operator asked
for exactly these presses — so every one is a real entry at that panel's own stake and expiry, and
eighteen presses on nine panels set to $1000 is $18,000 of entries. Set the stakes you intend first.

It needs a valid control map and a visible grid, and it does not need to be armed. Every press is
recorded as a ticket tagged `CONTROL_TEST` and counted against the hourly total, so an executor
armed afterwards is already throttled by what the test sent. **STOP** ends the run between presses.

Compare the resulting tickets against the broker's own order list: if cell 4's `HIGHER` ticket lines
up with an up entry on cell 4's instrument, the coordinates are right.

## The control map goes stale on purpose

A control map is a set of coordinates on a canvas the broker can re-lay at any time. The map records
the surface revision and the browser zoom it was measured at, and both the executor and the press
path refuse when either has moved. Measure again after:

- any browser zoom change,
- any window resize or layout change,
- a platform reload or re-login,
- loading a different calibration profile (this clears the map outright).

A stale map is reported as `CONTROL_MAP_STALE` rather than used.

## Confirmation

After a press the panel is captured again and compared with the capture taken immediately before.
A panel that visibly changed marks the ticket `CONFIRMED`. A panel that did not marks it
`UNVERIFIED`, which means the input events left the application and the broker's answer could not
be read — **not** that the order failed. Consecutive unconfirmed presses trip a breaker that
disarms the executor rather than continuing to send into the dark.

## Ticket states

| State | Meaning |
| --- | --- |
| `BLOCKED` | A gate refused before anything was sent. The reason codes say which. |
| `PAPER` | Every gate passed and PAPER mode recorded the decision without pressing. |
| `SENT` | Input events left the application; confirmation has not resolved yet. |
| `CONFIRMED` | The panel visibly reacted to the press. |
| `UNVERIFIED` | The press was sent and the panel did not visibly react. |
| `FAILED` | The press itself was refused or threw before any event was sent. |

`CONFIRMED` is a statement about the broker panel, not about the market. It means the press
landed and the panel reacted; it says nothing about whether the direction was right.

## The daily session guard is one more gate

Since Phase 9.5 this layer reads one extra permission each tick: the
[daily session guard](session-guard.md)'s `canOpenNewEntry`. When the day has reached its profit
target or loss limit, been stopped by the operator, been locked, or lost confidence in its own
accounting, a named refusal (`SESSION_DAILY_LOSS_LIMIT`, `SESSION_LOCKED_FOR_DAY`, …) joins the
block list and no press happens.

It only ever adds a refusal. Every gate described here still applies on its own, nothing about
AUTO, PAPER, Arm, Disarm, the control map, the Higher/Lower mapping or the all-controls test
changed, and no daily total can reach a stake, a score or a threshold — the guard has none of
those. The veto is sticky: once a stop has been read it stays in force until the engine
positively says otherwise, so an unreachable engine cannot lift a limit.

## PAPER mode is not the paper simulator

Two unrelated things in this project are called "paper".

This layer's `PAPER` mode answers *would this board have produced a press?* It runs every gate,
records an `OrderTicket` in state `PAPER`, and presses nothing.

The Phase 9 [paper simulator](paper-simulation.md) answers *what did the market do after this
board?* It lives in the Python engine, reads canonical prices rather than broker controls, and
resolves a trade to `WIN`, `LOSS`, `DRAW` or `INVALID` five seconds later on CapitalBear and
sixty on IQ Option. It imports nothing from this layer and cannot reach a broker.

`CONFIRMED` and `WIN` are therefore different claims about different things and are never
displayed together. The trading window renders them in separate panels with disjoint wording,
and a test asserts the two label vocabularies do not overlap.

## Limits

| Limit | Default | What it does |
| --- | --- | --- |
| `minRankScore` | 0.6 | Board's selected score must reach this. |
| `minEnsembleConfidence` | 0.55 | The named candidate's own Phase 7 confidence. |
| `minControlConfidence` | 0.8 | How cleanly that cell's controls were located. |
| `acceptBoardStatus` | `READY` | Which board statuses may be acted on. |
| `cooldownMs` | 60000 | Minimum gap between presses on a platform. |
| `maxOrdersPerHour` | 12 | Rolling hourly cap. |
| `maxUnverifiedInARow` | 3 | Unconfirmed presses before the breaker disarms. |

## What to expect in practice

Phase 8 acceptance on 2026-09-10 produced no board that named a leader. CapitalBear captures roughly
3–4 observations per second spread across nine slots, so an S5 bar often closes on one or two
samples, `qualityFit` stays low, and Phase 7 confidence lands near 0.02–0.13. Boards finalize as
`PARTIAL` far more often than `READY`.

An armed executor on the default limits will therefore sit idle most of the time, and the panel will
say why. Loosening the limits or opting into `PARTIAL` boards does not improve the analysis behind
them — it only lowers the bar the same weak evidence has to clear.

## Boundaries

- Only a workspace main frame can reach the execution channel. Calibration overlays and broker pages
  cannot, and the overlay is refused explicitly as well as by scope.
- `PlatformBrowserManager.pressPoint` is the only place in the application that produces input for a
  broker page. It refuses a hidden, paused, ungridded, re-navigated or re-zoomed surface, and
  re-checks the surface revision between mouse-down and mouse-up.
- The Python engine has no part in this. It ranks; it neither knows nor can trigger a press, and
  `tests/test_opportunity_safety.py` still asserts the ranking package binds no execution name.
