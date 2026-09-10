# Strategy engine

Phase 6 produces facts. Phase 7 interprets them. It reads one `FeatureBundle` and returns a regime, one evaluation per strategy, and a weighted ensemble — an analytical opinion with the evidence behind it and the objections that were considered.

It does nothing else. There is no order, no stake, no bankroll, no payout, no expected value, no entry timing and no broker control anywhere in `quant_engine.strategy` or its read API. Two tests enforce that: one scans every name the package binds or calls for execution vocabulary, and one asserts the package's entire import surface is `quant_engine`, the standard library, Pydantic and FastAPI — so there is no networking, no subprocess and no IPC in scope to reach a broker with.

## Architecture

`quant_engine.strategy` holds the whole layer. `models.py` carries the wire schema and the version constants, `common.py` the bounded scoring primitives and the market quality gate, `policy.py` the per-platform table, `regime.py` the classifier, `base.py` the contract every strategy shares, one module per strategy, `ensemble.py` the weighted vote, and `engine.py` the event-driven entry point.

Nothing here recomputes an indicator. No EMA, RSI, ATR, MACD, pivot or micro statistic is derived from raw candles inside a strategy: every number comes from the canonical qfe-v2 snapshots, and Phase 6 is never bypassed.

## Version contract

```
featureVersion   qfe-v2            (required, not merely reported)
regimeVersion    qst-regime-v1
strategyVersion  qst-strategy-v1
```

Every `RegimeSnapshot`, `StrategyEvaluation` and `EnsembleSnapshot` carries all three, and every persisted row carries them too.

`SUPPORTED_FEATURE_VERSION` is deliberately a literal rather than an import of Phase 6's `FEATURE_VERSION`. Every threshold in this layer is calibrated against the qfe-v2 formulas; tracking the Phase 6 constant would let a later formula change flow silently into strategies nobody re-checked. A bundle carrying any other version — including `qfe-v1`, which predates the basis-point correction — is refused outright with `UNSUPPORTED_FEATURE_VERSION`, and no strategy runs. Two versions of a feature are never pooled.

## Direction vocabulary

`UP` and `DOWN` carry directional evidence. `NEUTRAL` is a valid reading of a usable market in which the strategy found no edge. `SKIP` means the strategy was not eligible, or the data could not support an opinion at all. They are four distinct verdicts and are never collapsed: a NEUTRAL vote consumes weight in the ensemble because a member of the panel finding nothing is information, while a SKIP contributes nothing at all.

## Normalization and thresholds

Every mapping from a measurement to a score is bounded, monotone and named. There are no bare numbers inside expressions; a threshold is a module constant with a docstring explaining what it means.

`ramp(value, zero_at, one_at)` is a linear 0..1 ramp between two named points, and reads a descending measurement when `one_at` is the lower of the two. `signed_ramp(value, half)` is `x / (|x| + half)`, a smooth saturating map to −1..1 where `half` is the magnitude scoring ±0.5, so no single extreme reading can dominate an aggregate. `oscillator(value, midpoint, span, deadband)` maps RSI or %K around fifty, and reports a reading inside the deadband as no evidence rather than a faint one.

Basis-point evidence is normalized by the series' own volatility before it is scored — `atr14Bps`, or realized volatility while ATR is still warming, floored so a flat window cannot turn a rounding-sized move into an infinite number of ATRs. A slope becomes "typical bar movements per bar" and a distance becomes "typical bar movements". One set of constants therefore describes a five-second CapitalBear bar and a ten-minute IQ Option bar, and a 0.58 currency cross as much as an 8800 index. One-second evidence is normalized the same way against `microVol10sBps`.

A missing feature is absent, not zero. `None` lowers an aggregate's coverage and is reported as such; it never contributes a neutral vote that a real measurement would have to overcome.

## Market quality gate

Before any strategy may vote, `market_gate` checks the preconditions. A failure here is fatal for the whole panel and produces an explicit SKIP carrying the veto that caused it:

`UNSUPPORTED_FEATURE_VERSION` on the bundle or its primary snapshot; `MISSING_PRIMARY_FEATURES` when the bundle has no primary; `INVALID_PRIMARY_FEATURES` when the primary snapshot is INVALID; `PRIMARY_TIMEFRAME_MISMATCH` when the bundle's primary is not the platform's decision horizon; `CONTEXT_CHANGED` when the primary or any context belongs to a different platform, slot, asset, context or timeframe; `CHRONOLOGY_INVALID` when anything in the bundle closes after the bundle's own as-of time; and `INSUFFICIENT_DATA` below three closed primary bars.

Warming and degraded inputs are not failures. They set `qualityFit = clean × maturity × statusFactor`, where `clean` is the mean of the available `goodRatio10` and `meanCoverage10`, `maturity` is `barCount / 50` capped at one, and a DEGRADED snapshot keeps 0.7. DEGRADED is the honest steady state on live broker capture, so it discounts rather than rejects. When neither cleanliness measure exists yet, an explicit `UNMEASURED_QUALITY` of 0.5 stands in — claiming clean inputs nobody can see would be worse than admitting they are unmeasured.

`LOW_COVERAGE` is a soft veto: below half the expected seconds present the bar is a sketch of the market rather than a record of it, and the ensemble refuses to act on it while still reporting the regime.

Warming is handled per strategy rather than in bulk. Each declares the features it needs, and abstains if any of them is `None` — so a strategy that needs EMA20 abstains at twelve bars while `breakout_v1`, which needs only a ten-bar prior range, still evaluates. A micro strategy with adequate one-second coverage can read a series whose slow indicators have not warmed at all.

## Regime engine

The regime describes the environment a strategy will be operating in. It reports a primary label plus every supporting score, never a single label pretending the evidence was unambiguous.

**Trend** (signed, −1..1) is a weighted aggregate of the EMA stack (`ema5To9Bps`, `ema9To20Bps`, `ema20To50Bps`), price against EMA9 and EMA20, the EMA slopes, the five-, ten- and twenty-bar price slopes, RSI's distance from fifty at low weight, close location at low weight, and one-second velocity where coverage allows. The weights lean toward the persistent structure: a three-bar dip inside an eighty-bar advance is a pullback within a trend, not the end of one, and a regime that flipped on it would tell every strategy the wrong thing about where it is. Reading those fast members is the strategies' own job.

The aggregate is then multiplied by a coherence gain from the efficiency ratios. A stacked, sloping set of averages reached by a path that wandered is a weaker trend than the same stack reached in a straight line; a completely incoherent path keeps 35% of the directional evidence. RSI supports the reading and never carries it.

**Range** (0..1) combines low efficiency, high `choppiness14` across the conventional 38.2/61.8 boundaries, high `rangeOverlap5`, a tight EMA9/EMA20 separation and a flat ten-bar slope. A confirmed breakout then damps it directly. The absence of a breakout adds nothing: every quiet market lacks one, and counting it would put a floor under the range score in a trend. A range is never "RSI between 40 and 60" — a steady advance passes through those values too.

**Breakout** (signed, −1..1) requires that a prior extreme was actually taken out. `abovePriorHigh5/10/20` and `belowPriorLow5/10/20` are weighted 0.6/1.0/1.4, because a close beyond a twenty-bar extreme says more than one beyond a five-bar extreme. If no window shows a break the score is zero: touching a level is not breaking it. If the close points against the level it crossed the score is zero too.

The directional evidence is then multiplied by confirmation, and confirmation is conviction first and context second. The body-to-range ratio and the close location measured in the break's own direction carry the verdict; range expansion and path efficiency only scale what conviction established. A bar that ranged far and closed back near its own low with almost no body is a rejection of the level, and treating expansion as a peer of the body would have scored that rejection as a breakout. An unconfirmable cross is worth `BREAKOUT_CONFIRMATION_UNKNOWN` — a fact, not yet a breakout.

**Noise** (0..1) combines `signFlipRate10`, low efficiency, high overlap, high choppiness, the one-second flip rate where coverage allows, and the quality and coverage of the inputs themselves.

**Volatility states** (0..1 each) compare only measurements available within one snapshot: `rangeExpansion` (true range against ATR), the ratio of ten- to twenty-bar realized volatility, and Bollinger width in ATR units. No long-term percentile rank is invented, because Phase 6 supplies no history that would support one.

### Label selection and confidence

Noise is settled first and is not a peer of the others: at or above `NOISY_FLOOR` the label is NOISY, because a path that incoherent cannot support a structural reading at all and whatever the trend and range measures appear to say about it describes the noise. Otherwise the strongest label that clears its own claiming floor wins — breakout 0.34, trend 0.32, range 0.52, volatility 0.60 — and below every floor the regime is `UNCERTAIN`, which is a real answer and not a failure.

Confidence is `strength × consistency × qualityFit`. Consistency falls with the share of the evidence that *contradicts* the winning label, from an explicit table: two labels that merely coexist are not in conflict, so a breakout up and a trend up do not talk each other down, while a range and noise do conflict because they disagree about whether the structure can be read at all — the difference between an excursion worth fading and a path worth leaving alone. A fully contested label keeps 65% of its strength; being contested makes a reading weaker, not absent.

`directionBias` blends trend and breakout evidence and is NEUTRAL in a noisy regime or below `REGIME_BIAS_FLOOR`.

## Strategy catalog

Each strategy declares the features it requires, a regime-fit table, and its own documented minimum evidence before it will name a direction rather than NEUTRAL. Below `MIN_REGIME_FIT` it abstains outright rather than adding a weak vote the ensemble would still have to carry.

**`trend_follow_v1`** — participates when trend evidence is coherent. Reads the EMA stack ordering, EMA9/EMA20 separation, price against EMA9, the EMA and price slopes, RSI, close location, one-second velocity, and higher-timeframe agreement weighted by platform policy, then applies the same coherence gain the regime uses. Vetoes: a convincing range regardless of the label, extreme noise, and `CHASING_RISK` when true range exceeds 2.6 ATRs — the move has already happened, and this layer does not model entry timing. Fit: 1.0 in a trend, 0.8 in a breakout, 0.10 in a range, 0.05 in noise.

**`momentum_continuation_v1`** — RSI alone is not momentum. Evidence is grouped into families that measure different things — oscillators, rate of change, MACD, five-bar slope, one-second push — each scored on its own. At least three families must be measurable and at least three must point the same way; otherwise the result is NEUTRAL with `FAMILIES_DISAGREE` rather than an average that hides the conflict. Vetoes: sign flip rate at or above 0.75, choppiness at or above 68, range overlap at or above 0.85.

**`breakout_v1`** — the strategy form of the regime's breakout reading, with its own thresholds and an extreme-noise veto. `NO_BREAK` when price is still inside its prior range, `BREAK_REJECTED` when the bar points the other way, `WEAK_CONFIRMATION` when the bar that crossed the level carried no conviction.

**`mean_reversion_v1`** — eligible only where the regime supports ranging behaviour: fit 1.0 in a range, 0.75 in volatility compression, and 0.0 in a trend or breakout. Reads %B, the Bollinger z-score, RSI and stochastic beyond a deadband, and distance to the nearest confirmed pivot, all inverted, then scales by the regime's own range score. It carries a second, independent guard on raw trend pressure for the case where the label reads RANGE while the trend score disagrees: an oversold reading inside a working trend is the trend, not an excursion to fade.

**`micro_impulse_v1`** — the one-second stream, where it is dense enough to read. Coverage comes first: below 60% of the last ten seconds actually existing it abstains outright. A one-second sign flip rate at or above 0.65 abstains too. Evidence is the three- and five-second returns, the three- and five-second velocities, one-second acceleration and the closed bar's agreement, scaled by one-second efficiency — a fast series that keeps changing its mind is volatility, not direction. Coverage multiplies confidence a second time on purpose: a barely-admissible stream may be read, but never with the conviction of a complete one.

**`trend_pullback_v1`** — separates two horizons Phase 6 measures independently. The host trend comes from EMA20 against EMA50, the twenty-bar slope, the EMA20 slope and higher-timeframe agreement; the pullback from where price sits against EMA5, whether the oscillators have reset, distance to a level, and whether price is turning back into the trend. Direction always resumes the host trend. Below `MIN_RETRACE` there is no pullback yet; beyond `MAX_RETRACE` the move against the trend is larger than the trend's own bars and the strategy declines to call it either way. Fit is 0.0 in a range and in noise, so it never fires where the trend it would resume does not exist.

A reset inside a trend is relative, not absolute: a sustained advance holds RSI in the seventies, and demanding it fall to the classic oversold region would mean only recognising pullbacks in trends that had already ended. RSI and stochastic describe the same reset at different sensitivities, so the clearer of the two carries it.

## Ensemble

Not a majority vote. Each eligible strategy is given a capacity and casts a fraction of it equal to its own conviction:

```
capacity = baseWeight × regimeFit × qualityFit × platformFit × noiseDamping
vote     = directionSign × confidence × capacity
```

`confidence` on an evaluation is the strategy's own conviction only; regime, quality and platform fit are applied once, here, so multiplying them in earlier would double-count them.

Base weights are initial heuristics, not optimized values. Nothing was fitted to data; they encode only how much independent information each member is expected to add, and are deliberately close together so no single one can carry the panel.

```
trend_follow_v1           1.00
momentum_continuation_v1  0.85
breakout_v1               1.00
mean_reversion_v1         0.85
micro_impulse_v1          0.90
trend_pullback_v1         0.90
```

Regime adaptation is expressed once, as each strategy's own regime-fit table, which multiplies its capacity: a trend raises trend-follow and pullback and zeroes mean reversion; a range raises mean reversion and all but removes trend-follow; a breakout raises breakout and zeroes mean reversion; noise reduces everything. Expressing it a second time as a separate weight table would apply the same adjustment twice.

Noise damping is separate and global. It starts only where noise exceeds `NOISE_TOLERANCE`, because range and noise share their inputs and an ordinary, perfectly tradeable range already scores around a half — damping from zero would punish every range-appropriate strategy for the conditions that make it applicable, on top of the regime fit that has already accounted for them.

```
active        = up + down + neutral
net           = up − down
agreement     = |net| / active
disagreement  = min(up, down) / (up + down)
```

`agreement` is the net directional vote over the whole active panel, so a panel that mostly found nothing reports low agreement even when its one voter was certain. `disagreement` is the share of the directional weight that voted the other way.

### Vetoes

Ensemble vetoes are ordered so the reported one names the real cause, and a SKIP always carries at least one — output is never suppressed without an explanation.

`EXTREME_NOISE` at or above 0.75, whatever any single strategy says. `NO_ELIGIBLE_STRATEGY` when every member abstained. `INSUFFICIENT_STRATEGY_WEIGHT` when total capacity is below 0.60, because one heavily damped opinion is not a panel. `CONFLICT_TOO_HIGH` at a disagreement of 0.45 or more: an almost even split is not a weak signal, it is an unresolved one. The gate's own `LOW_COVERAGE` overrides all of them.

With no veto, agreement below `MIN_AGREEMENT` gives NEUTRAL, and otherwise the sign of the net vote gives UP or DOWN.

### Confidence is not a probability

```
confidence = agreement × (1 − disagreement) × regimeFactor × qualityFit × breadth
```

`regimeFactor` keeps a floor of 0.40 so an uncertain regime discounts rather than erases — the strategies already carry their own regime fit. `breadth` is eligible strategies over `FULL_PANEL`, capped at one.

It measures how much usable, agreeing evidence the panel produced. It is not a probability of winning, and it is deliberately not called one: Phase 7 has no calibration and no outcome history behind it. Nothing in the schema carries a `winProbability`, an `edge`, an expected value, a payout or a position size. Calibration belongs to replay, backtesting and shadow phases.

## Platform policy

Policy is data, kept apart from the strategy arithmetic, so a policy change never edits a formula and a formula change never quietly re-tunes a platform.

| | CapitalBear | IQ Option |
| --- | --- | --- |
| Primary timeframe | S5 | M1 |
| Micro weight | 1.00 | 0.35 |
| Context weights | M1 0.60, M5 0.20 | M5 0.70, M10 0.45 |
| `micro_impulse_v1` fit | 1.00 | 0.35 |
| Other strategies | 0.80–0.90 | 1.00 |

CapitalBear settles five-second bars, so the one-second stream is a first-class input and M1 is the structure a five-second bar sits inside; bar-count-hungry strategies are worth slightly less because fifty S5 bars span four minutes of a fast, thin series. IQ Option settles one-minute bars, so M5 and M10 carry real structural weight and the last few seconds of a sixty-second bar are secondary confirmation — the micro strategy still runs there, as a diagnostic, at a much lower platform fit.

Higher-timeframe agreement is used as weighted evidence, never as a gate. A strategy is not required to find perfect alignment across timeframes, only to be told how much the wider structure concurs.

## No-lookahead

Phase 7 inherits Phase 6's guarantee and adds nothing that could break it. A strategy reads exactly one `FeatureBundle` and never fetches a timeframe for itself; contexts are only those the feature engine already joined as-of the primary close. The gate refuses any bundle whose primary or context closes after the bundle's own as-of time. Evaluation is triggered by a closed primary snapshot, so nothing is ever computed from a forming bar.

Evaluation is pure. `evaluate_bundle` consults nothing outside the bundle, and the same bundle always produces byte-identical output.

## Event flow, reset and isolation

```
CLOSED candle → FeatureSnapshot → (primary timeframe only) FeatureBundle
              → RegimeSnapshot → six StrategyEvaluations → EnsembleSnapshot
```

`MarketEngine.persist_events` already hands each canonical record to the feature engine exactly once. When that produces a snapshot on the platform's primary timeframe — S5 for CapitalBear, M1 for IQ Option — the bundle is evaluated and the results are stored in the same pass. A closed M5 or M10 updates the context a later primary close will read and produces no ensemble of its own. Evaluation is never driven by a UI poll; the read API returns what the last event produced.

A bundle whose identity and as-of time have already been evaluated returns the stored snapshot instead of recomputing it, so one primary close produces exactly one ensemble.

State is keyed by `(platform, slotId)` and holds only the last 32 ensembles per slot — a diagnostic convenience; Parquet is the durable record. `MarketEngine.reset_slots` resets Phase 5, Phase 6 and Phase 7 together, and a new `contextId` starts a fresh history rather than extending the old one, so no regime, evaluation or ensemble from a previous asset or context can survive. CapitalBear slot 1 and IQ Option slot 1 are separate state with separate policy.

## Storage and API

Three categories are appended alongside the existing ones, reusing the same sanitized asset partition so raw asset text never reaches a path:

```
regimes/platform=capitalbear/asset=EUR_USD_OTC-<hash>/date=2026-09-10/<uuid>.parquet
strategy_evaluations/...
ensembles/...
```

Every row carries `platform`, `assetName`, `slotId`, `contextId`, `asOf`, `featureVersion`, `regimeVersion` and `strategyVersion`, and each category is reloaded through its own model. An ensemble round-trips through Parquet with its nested regime, evaluations, reasons and vetoes intact. No screenshot or raw capture reaches this store.

Three local-only read endpoints sit behind the same trust boundary as the market and feature APIs; browser-origin requests are rejected with 403 and a read attempted while an ingestion thread owns the engine returns a retryable 429.

`GET /api/strategy/state` returns the version contract and a compact per-slot summary: regime, direction, confidence, eligible and active counts, each strategy's vote, and any vetoes. `GET /api/strategy/{platform}/{slotId}` returns the latest regime, every evaluation and the ensemble. `GET /api/strategy/{platform}/{slotId}/history?limit=` returns bounded recent ensembles. There is no write route and no execution route.

Under **Developer diagnostics** the desktop workspace shows two extra lines per slot, for example:

```
Regime: TREND_UP 72% · Ensemble: UP 68% · v qst-strategy-v1
Votes: Trend UP 0.71 · Momentum UP 0.55 · Breakout SKIP · MeanRev SKIP · Micro UP 0.62
```

An unreachable engine is reported as unreachable rather than rendered as a missing opinion.

## Expected behaviour

The engine abstains often, and that is the intended behaviour rather than a gap to close. SKIP, NEUTRAL and low confidence are preferable to a forced direction, and nothing here is tuned for signal frequency or for any claimed win rate. A benchmark over eighteen slots evaluates roughly 2,100 ensembles per second — close to 12,700 strategy evaluations — in bounded memory, which is negligible beside the capture and OCR load. Live, eighteen slots produce at most a couple of ensembles per second.
