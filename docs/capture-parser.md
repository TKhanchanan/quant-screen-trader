# Capture and parsing

Configure enabled assets, save/load a calibration profile, return to the browser and choose **Start observation**. The toolbar offers 250, 500, 1000 and 2000 ms target intervals. CapitalBear defaults to 500 ms; IQ Option to 1000 ms. Sampling does not start automatically. Missing calibration, hidden/unavailable platform views and calibration overlays prevent capture. Disabled slots remain idle.

Pixel ROI is computed from normalized bounds and current browser dimensions in device-independent pixels. The platform's owned `webContents.capturePage(rect)` captures only that ROI; there is no desktop screenshot path. Native image scaling is normalized to at most 1024×1024 pixels. Preprocessing makes a separate grayscale buffer, stretches contrast and optionally thresholds it. Original images remain transient in main-process memory and are not modified or saved.

See Electron's [capturePage contract](https://www.electronjs.org/docs/latest/api/web-contents#contentscapturepagerect-opts) for native capture ownership. The account-free check exercises the real capture API and OCR, not an OCR stub.

The scheduler has no pending capture queue. It permits one job per slot and uses a round-robin slot cursor and one active job per platform. This bounds OCR work and shares time between enabled slots. Different platforms have independent workers. A job must finish before the next slot starts; target intervals are ceilings on requested frequency, not guaranteed rates. A hung platform job pauses that platform's capture work; no watchdog is introduced in these phases.

Configuration/preset/profile changes invalidate in-flight results while retaining busy guards. Visibility, navigation, reload, overlay and browser-bounds changes reset observation contexts. Late results from an old context cannot update slot cards or enter the batch queue. The queue retains the latest observation per platform/slot, at most 18, plus at most one 18-observation HTTP batch in flight. Batches flush every 250 ms with a 2-second request timeout. Replaced and failed deliveries increment drop counters. There is no stale backlog retry loop.

`parsePrice` accepts positive finite decimal notation with varying precision. It rejects malformed OCR such as `1.O845`, separators, exponents, signs, NaN, infinity and zero. No character corrections are applied. Payout uses ratios (`82%` → `0.82`). Timer uses duration seconds (`1:23` → `83`), never inferred expiry.

Slot cards display observation state, source, accepted price, quality, age, one-second buffer count and M1 sample/state progress. Developer diagnostics show the typed observations, parser and calibration context, normalized/pixel ROI, capture/parse latency, queue lag and drops. No screenshot preview/export or retention subsystem is needed because images are never stored. There is no trading-signal UI.

Run `node scripts/market-smoke.mjs` for an isolated native synthetic-text capture and real local OCR check. It uses a temporary OS profile and no account. Its temporary profile is outside the repository. No screenshots are written.
