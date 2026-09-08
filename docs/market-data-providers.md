# Market data providers

Electron owns browser interaction. `MarketDataProvider` has start, stop, observe and health operations, implemented by DOM, visual, replay and synthetic providers. Remote pages retain their existing sandbox, origin restrictions, isolated persistent partitions and lack of application preload.

Live selection tries rendered DOM fields first, then native capture with local Tesseract OCR. Replay and synthetic providers are explicitly selected by account-free tests; they are never automatic live fallbacks. Synthetic fixtures have deterministic clocks and IDs. Replay validates supplied sanitized observations and rejects platform, slot or asset mismatches. Both use the same Python builder as live data.

The DOM adapter uses fixed visible asset/current-price/payout/expiry selectors, restricted to the calibrated slot. It excludes forms, inputs and editable elements. It reads no cookies, storage, page-state objects, requests, headers or private APIs. Only exact configured asset matches and strictly formatted numeric strings leave the DOM script. These selectors are conservative adapter candidates, **not broker-validated selectors**. A loaded page does not establish authentication or data reliability.

The visual adapter crops the owned platform WebContentsView using normalized calibration. Local English language data ships as an npm dependency; OCR does not download a model at runtime. OCR accepts one unambiguous numeric price line and an exact asset match. Multiple axis labels, split asset labels, ambiguous text and low confidence fail closed. Whole-chart OCR is not a validated broker price extractor. Narrow calibration to the relevant chart UI without account information; field-specific broker profiles remain necessary if chart labels are ambiguous.

Provenance is mandatory: platform, slot, configured asset, source enum, observation and parse timestamps, context UUID, calibration UUID, parser version, confidence and latency. Unknown observation fields are rejected. Raw DOM text, HTML, OCR output and images are never part of the engine contract. The source enum always distinguishes DOM, VISUAL, REPLAY and SYNTHETIC. Source changes also create separate series identities.

The portable OCR adapter has passed a native Electron synthetic-image check for asset, price, payout and timer. Neither broker's authenticated chart extraction has been validated in this change. Do not interpret fixture confidence as live-market accuracy.


Instrument discovery now uses platform-specific `AssetDetector` adapters before price providers. They return a strict nine-slot detection result with source, evidence, confidence and timestamp. A rendered-DOM check against the user-opened broker pages found one canvas per page and no instrument-label DOM nodes. The DOM adapters are validated with synthetic chart-container fixtures; authenticated canvas extraction still requires correctly aligned OCR label regions. See [asset detection](asset-detection.md).
