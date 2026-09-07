# Data quality

Quality is a set of components, not an opaque overall score:

| Component | Definition |
| --- | --- |
| confidence | Parser confidence, forced to zero without an exact configured/visible asset match |
| freshness | `max(0, 1 - (parsedAt - observedAt) / 3000 ms)` |
| completeness | Available price/payout/timer fields divided by three |
| sourceReliability | Conservative configured prior: visual 0.8, DOM/replay/synthetic 1; not measured accuracy |
| latencyMs | Observation-to-parse elapsed wall-clock milliseconds |

The parser emits STALE above 3000 ms, INVALID without a valid price, UNCERTAIN below 0.8 confidence, otherwise GOOD. An exact visible asset match is required for GOOD. Payout/timer absence lowers completeness but does not invalidate an otherwise usable price. No expiry is inferred. OCR confidence is an engine heuristic and does not imply a probability of market-price correctness.

Python independently requires a positive finite price, confidence ≥0.8 in both parser and quality, positive freshness, reliability ≥0.8, GOOD or DEGRADED state, and parse delay ≤3 seconds. The live ingestion boundary separately checks wall-clock age and rejects future observations. No sample is made from UNCERTAIN, STALE or INVALID data, irrespective of an attached numeric price. Provider exceptions are isolated to the slot.

UI READY means the latest observation passed the parser gate. Engine receiving status and series counts separately confirm ingestion. Missing observations turn stale after 3 seconds. Developer diagnostics retain quality components and timings; no raw OCR text, page state, session secrets or account images are exported.

Candles propagate degradation and missing-second coverage. Forming candles include their still-unobserved seconds in the denominator and therefore usually have low coverage. A fully covered candle is GOOD only if all accepted inputs were GOOD. No indicator, strategy or trading decision is implemented in these phases.
