# Market data builder

Python owns the sole aggregation implementation. `MarketObservation` passes the quality gate before becoming a `PriceSample`. Raw validated observations, including uncertain ones, retain their provenance; uncertain observations do not create price samples.

Live HTTP batches contain 1–18 observations. Browser-origin requests are forbidden. The loopback service accepts one processing batch at a time and responds 429 under contention, allowing Electron to continue keeping fresh observations. Engine timestamps reject future live observations and observations older than 3 seconds. Replay/synthetic clocks are supplied by fixtures instead of the wall clock. Batch responses report accepted/rejected counts and per-slot series progress.

Each of the maximum 18 platform/slot builders has bounded deques: 3600 tick samples, 3600 one-second records and 3600 closed candles by default. The constructor accepts a capacity. At most four candles are forming. Second-coverage sets contain at most 600 entries per timeframe. The consumer drains a bounded emission buffer after each observation. Recent buffers are not long-term history.

One-second output selects the latest valid observation within each UTC second. `bucketTime` identifies that second's beginning; `timestamp` retains the selected observation's actual time. Tick samples have null `bucketTime`. One-second output is emitted only after its boundary. No averaging or forward filling occurs.

S5, M1, M5 and M10 OHLC all derive directly from the same validated tick-like samples. This intentionally preserves intrasecond extrema that latest-only one-second resampling would discard. It also avoids independent aggregation implementations. Timeframes align to Unix-epoch UTC multiples of 5, 60, 300 and 600 seconds; closeTime is exclusive. An observation exactly at closeTime belongs to the next candle.

The first observation at an equal millisecond timestamp wins. Out-of-order or watermark-late observations are rejected; closed history is never rewritten. Timestamps are millisecond precision. `advance(timestamp)` is an explicit availability watermark, and closes only boundaries it has reached. Live idle advancement uses wall time minus a 3-second delivery allowance. Thus a live closed candle can appear up to several seconds after its boundary, never before it. Replay tests advance their own event clock. Candles remain FORMING until closure.

Coverage is distinct observed UTC seconds / timeframe seconds. `expectedSamples` is the number of expected **one-second coverage opportunities**, not the number of requested captures. `sampleCount` is accepted raw observations. `gapDurationMs` counts uncovered seconds in the entire candle, including not-yet-observed forming seconds. `missingSeconds` counts gaps between accepted samples. Empty candles are omitted, and absent buckets remain gaps. Partial coverage or any degraded input marks a candle DEGRADED. Uncertain inputs are excluded entirely.

Platform, slot, canonical asset, context UUID, calibration UUID and source type define series identity. A change clears recent history and discards partial forming state after advancing elapsed old boundaries. Closed historical records remain in Parquet with their original identity. Preset, calibration, navigation and resize resets are explicit contexts. Disabled or disconnected slots cannot fabricate continuity.

## Storage

SQLite settings and its committed migrations are unchanged. DuckDB writes typed, Zstandard-compressed Parquet under the existing external `market-data` directory:

```
observations/platform=capitalbear/asset=EUR_USD_OTC-<hash>/date=2026-09-07/<uuid>.parquet
samples/...
seconds/...
candles/...
```

Canonical asset names and provenance are columns, not inferred from path names. Sanitized, length-limited asset folders have a hash suffix to avoid collisions and traversal. Observation timestamps are normalized to UTC on export and restored as timezone-aware values on reload. Writes use temporary files and atomic rename. A shared 4096-record buffer groups output by category/platform/asset/date, avoiding per-capture files. It flushes when full and on normal engine shutdown. Low-volume data remains in memory until then; abrupt termination can lose unflushed records. Failed writes retain pending buffers and surface engine unavailability. This is buffered local research storage, not a transactionally durable event log.

`MarketStorage` defines append/flush. `ParquetStorage.reload` validates reloaded records. DuckDB can query the typed files directly through its [Parquet API](https://duckdb.org/docs/stable/data/parquet/overview). Never write account/session metadata or images to these datasets.

Retention defaults to retaining all data. `retention(before=...)` previews matching files by file modification time; deletion additionally requires `delete=True`. There is no automatic deletion job. Batch size and buffer capacity are constructor configuration; sampling interval is workspace runtime configuration.

Tests cover deterministic trends, rapid movement, gaps, duplicate/out-of-order timestamps, uncertainty, asset/calibration/source changes, no look-ahead, all candle boundaries, bounded memory, Parquet round trips and replay equivalence. `node scripts/python.mjs services/quant-engine/tests/benchmark_market.py` measures an account-free 18-slot workload.


The continuation's isolated loopback integration check verified all 18 streams with explicitly SYNTHETIC observations, completed M10 candles and typed Parquet reload after normal shutdown. This establishes integrated builder/storage behavior without claiming a real authenticated price feed; see the dated [development verification](development.md#acceptance-continuation--2026-09-08).
