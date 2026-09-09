# Quant feature engine

Phase 6 turns the canonical Phase 5 series into quantitative facts. It classifies nothing: there is no direction, no signal, no score and no ranking anywhere in this layer. Regime detection and strategy belong to Phase 7 and consume the snapshots described here.

## Architecture

`quant_engine.features` holds the whole engine. `math.py` carries the safe numeric primitives, `trend.py`, `momentum.py`, `volatility.py`, `structure.py`, `noise.py` and `micro.py` carry one family of indicators each, `models.py` the wire schema, and `engine.py` the streaming state machine. No second candle builder exists; `TimeSeriesBuilder` remains the only aggregation implementation.

Feature computation is event-driven, never polled. `MarketEngine.persist_events` drains the builder's bounded emission buffer and, as it stores each canonical record, hands it to the feature engine exactly once: a completed one-second `PriceSample` to `ingest_second`, a CLOSED `Candle` to `ingest_candle`. A CLOSED candle produces exactly one `FeatureSnapshot`, which is appended to the `features` storage category in the same pass. `MarketEngine.reset_slots` resets feature state for the same platform and slots it drops.

State is a dictionary keyed by `(platform, slotId)`. Each entry owns an identity of `(assetName, contextId)`, a `MicroState` and four `TimeframeState` objects. A snapshot is addressed by platform, slot, asset, context, timeframe, feature time and feature version together; `slotId` alone never identifies a series.

## No-lookahead guarantees

A feature at time T uses only information that existed at or before T. Four mechanisms enforce it.

FORMING candles are refused outright. Persistent indicators — EMA, RSI, ATR, MACD, stochastic, pivots — advance only on CLOSED bars, so no rolling value depends on how often the capture loop happened to run inside the bar.

A CLOSED candle whose `closeTime` is at or before the last one already applied is rejected and counted, so replayed or late history cannot rewrite an emitted snapshot. Snapshots are constructed once and never mutated; appending future candles leaves every earlier snapshot model-dump identical.

Prior ranges exclude the current candle by construction: `prior_range` slices `[-(window + 1):-1]`, so `priorHigh20` is the highest high of the twenty bars *before* the current one.

Swing pivots are confirmed, never provisional. A candidate at the centre of a five-bar window becomes a pivot only after both bars to its right have CLOSED, which happens exactly two bars later. Support and resistance are derived only from confirmed pivots.

## Feature catalog

Distances are basis points, `bps(a, b) = (a / b - 1) * 10000`, so features stay comparable across a 0.58 currency cross and an 8800 index. A quantity that is itself a price span rather than a distance between two prices — true range, ATR, the MACD lines — is normalized as `value / close * 10000`, which is also basis points. Anything named `…Bps` is basis points; a bare ratio would be four orders of magnitude smaller and silently understate volatility. Nothing hard-codes decimal precision. Every division is guarded: a zero or invalid denominator yields `None`, never `0`, and NaN and infinity are rejected by the models themselves.

**Price action** — `return1Bps`, `return3Bps`, `return5Bps`, `logReturn1Bps`, `bodyBps`, `rangeBps`, `bodyToRange`, `upperWickToRange`, `lowerWickToRange`, `closeLocation`, `trueRangeBps`, `gapFromPreviousCloseBps`. Body is `close - open`, range is `high - low`, `closeLocation` is `(close - low) / (high - low)` and stays in `0..1`. True range is `max(high - low, |high - prevClose|, |low - prevClose|)`; the first bar of a series has no previous close and uses `high - low`.

**Trend** — EMA 5, 9, 20 and 50 with `alpha = 2 / (period + 1)`, each seeded by `SMA(period)` and `None` before its seed completes. Raw values are exposed for debugging alongside normalized distances (`priceToEma*Bps`) and relationships (`ema5To9Bps`, `ema5To20Bps`, `ema9To20Bps`, `ema20To50Bps`). Each EMA also reports `ln(EMA_now / EMA_3bars_ago) / 3 * 10000` as `ema*Slope3`, in basis points per bar. `priceSlope5`, `priceSlope10` and `priceSlope20` are OLS slopes of `log(close)` against `x = 0..N-1`, likewise in basis points per bar. These are numbers, not verdicts.

**Momentum** — Wilder `rsi14`, `roc5Bps`, `roc10Bps`, `stochK14` with `stochD3`, and MACD 12/26/9 exposed both raw and normalized by the current close (`macdBps`, `macdSignalBps`, `macdHistogramBps`). RSI seeds from the mean of the first fourteen gains and losses, then smooths with `(prev * 13 + current) / 14`. All gains gives 100, all losses gives 0, and a perfectly flat window — where Wilder's ratio is undefined because neither side has any pressure — is documented and tested as the neutral 50. Stochastic returns `None` when the fourteen-bar range is zero, and `%D` waits for three real `%K` values.

**Volatility** — Wilder `atr14` (SMA seed over the first fourteen true ranges, then `(prev * 13 + current) / 14`) and its normalized `atr14Bps`; `realizedVol10Bps` and `realizedVol20Bps` as the population standard deviation of log returns in basis points, deliberately not annualized because an annualization factor would encode a timeframe assumption that does not hold across S5 through M10; Bollinger 20/2 with population standard deviation, giving `bbMiddle`, `bbUpper`, `bbLower`, `bbWidthBps`, `bbPercentB` and `bbZScore`; and `rangeExpansion` as the current true range over ATR. A flat window has real bands and a zero width, but `bbPercentB` and `bbZScore` are `None` because they divide by a dispersion that does not exist.

**Structure** — prior highs and lows over 5, 10 and 20 bars with their distances, plus the plain breakout facts `abovePriorHigh5/10/20` and `belowPriorLow5/10/20`. The sign convention is that a distance stays positive while the level is still outside price: resistance is `(level / close - 1) * 10000`, support is `(close / level - 1) * 10000`. Confirmed pivots feed `nearestSupportDistanceBps`, `nearestResistanceDistanceBps`, `supportTouches` and `resistanceTouches`. Levels within `0.25 * ATR14` of each other are treated as one; before ATR exists the tolerance is zero, so only exactly equal levels group and no clustered level is invented.

**Noise** — `efficiencyRatio10` and `efficiencyRatio20` (net displacement over distance travelled, `0..1`), `choppiness14` using `100 * log10(sum(TR14) / (highestHigh14 - lowestLow14)) / log10(14)` clamped to `0..100`, `signFlipRate10` over the last ten non-zero returns, and `rangeOverlap5`, the mean overlap of adjacent candle ranges normalized by the smaller range. A pair where either candle has no range cannot express an overlap fraction and is skipped rather than counted as total agreement. None of these carry a TRENDING or RANGING label.

**Micro** — built from the canonical one-second stream only, never from capture frames. Seconds are addressed by absolute epoch second, so a missing second stays missing and is never bridged by reusing the previous price. Returns over 1, 3 and 5 seconds; `microVelocity3s` and `microVelocity5s` as `ln(p_now / p_Nago) / N * 10000` in basis points per second; `microAcceleration1s` as `r(t) - r(t-1)` where each `r` is the one-second return in basis points; population volatility over 5, 10 and 30 seconds computed only from consecutive-second pairs; ranges over the same windows; `microEfficiency5s` and `microEfficiency10s`; `microSignFlipRate10s`; and `microCoverage10s` and `microCoverage30s`, which report the share of the interval for which a real second exists — seven seconds out of ten is 0.7, not a gap filled to 1.0.

**Time context** — `timeframeSeconds`, `isOTC`, cyclic UTC `hourUtcSin/Cos` and `minuteUtcSin/Cos`, and `minutePhase` as `(featureTime % 60000) / 60000`. No named trading sessions and no economic-event assumptions.

## Warm-up and readiness

`status` is explicit. `WARMING` means fewer than fifty CLOSED bars, the point at which the slowest member of the catalog (EMA 50) can first exist; unavailable indicators report `None` throughout. `DEGRADED` means the history is long enough but the recent inputs are not clean — `goodRatio10` or `meanCoverage10` below 0.8. `READY` means both. `INVALID` is reserved for a candle that is not internally consistent, such as a high below its low; that candle is reported and counted but never applied to any rolling state.

Under live capture, coverage is rarely a full 1.0 for every second of a bar, so `DEGRADED` is the honest and expected steady state on real broker data. Nothing upgrades it silently.

`FeatureQuality` carries `goodRatio10/20`, `meanCoverage10/20`, `gapBars10/20`, `missingSecondsRecent`, `currentCandleQuality`, `historyBars` and `hydratedBars`. It is metadata about the inputs and is never converted into a trading score.

## Historical hydration

A restart does not have to wait fifty fresh bars when trusted history already exists. The first time a timeframe sees a live candle, `ParquetStorage.load_history` returns a bounded tail for that exact series and the bars seed the indicators. The rules are deliberately narrow: the platform, the exact `assetName` and the timeframe must match, so OTC and non-OTC never mix; only CLOSED candles from live DOM or VISUAL capture qualify, so replay and synthetic data can never warm a live engine; only candles whose `closeTime` is at or before the current candle's `openTime` are eligible; duplicates at the same open time are resolved deterministically by quality, then coverage, then sample count; and only the contiguous tail is used, so a gap ends the history rather than being bridged. Hydration seeds state only — the emitted snapshot always carries the current `slotId`, `assetName` and `contextId`, so a previous session's context never leaks forward. Hydration is attempted once per series and is best-effort: if storage is unavailable the engine simply stays `WARMING`.

## Multi-timeframe

S5, M1, M5 and M10 are computed independently and each keeps its own latest snapshots. `FeatureBundle` is a read model that joins them as-of the primary. CapitalBear's primary timeframe is S5 and IQ Option's is M1, matching each platform's decision horizon.

The as-of join is strict. A context timeframe may only contribute a snapshot whose `featureTime` is at or before the primary's. An S5 bar closing at 10:03:25 therefore attaches the M5 that closed at 10:00:00, never the one that will close at 10:05:00. Because the bundle is computed on demand from a short bounded history of snapshots per timeframe, it always attaches the freshest context that was genuinely already available. Phase 6 stops there: it supplies the per-timeframe snapshots and computes no alignment, agreement or multi-timeframe direction.

## Identity, reset and bounded memory

Any change to `(assetName, contextId)` for a slot discards the entire state and starts fresh, so no EMA, RSI, ATR, MACD, pivot, snapshot or one-second sample from a previous asset or context survives. CapitalBear and IQ Option, slot 1 and slot 2, and `EUR/USD` and `EUR/USD OTC` are all separate state.

Nothing is unbounded. Each timeframe retains 256 CLOSED candles, eight recent snapshots and at most 64 pivot highs and lows; micro history holds 120 seconds. A benchmark over eighteen slots and four timeframes confirms the caps hold under sustained load.

## Storage and API

CLOSED snapshots are appended to the `features` category and follow the existing partition layout, reusing the same sanitized asset folder so raw asset text never reaches a path:

```
features/platform=capitalbear/asset=EUR_USD_OTC-<hash>/date=2026-09-09/<uuid>.parquet
```

A snapshot round-trips through Parquet and back through Pydantic without semantic change, and every persisted snapshot carries `featureVersion`. The constant is `qfe-v1`; changing any formula requires changing it, so a later backtest can never silently mix two definitions of the same feature.

Two local-only read endpoints sit behind the same trust boundary as the market API, and browser-origin requests are rejected. `GET /api/features/state` returns per-slot readiness for every active slot: asset, context, primary timeframe, micro sample count, and per-timeframe bar count, hydrated bars, status and feature time. `GET /api/features/{platform}/{slotId}`, optionally filtered by `timeframe`, returns the latest snapshots and the as-of bundle. There is no network-facing trading service.

The desktop workspace shows a one-line summary under **Developer diagnostics** — for example `Quant: S5 WARMING 18 · M1 READY 64 · v qfe-v1` — and reports an unreachable engine rather than inventing state.
