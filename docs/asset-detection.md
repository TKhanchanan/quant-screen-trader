# Automatic asset synchronization

Use **Sync Assets** to inspect the displayed instruments and apply confident identities to AUTO slots. **Auto Sync Assets** repeats the check every 3 seconds by default and requires three consistent, high-confidence checks before changing an asset. Manual typing remains available in Asset Setup. Editing an asset or its enabled checkbox locks that slot to MANUAL; switch its mode back to AUTO to allow synchronization. Presets save and restore these modes.

## Detection and mapping

CapitalBear and IQ Option keep separate platform mappings. Production Sync first isolates the visible top-tab rectangles twice and requires their count, physical order and geometry to remain stable. At least three opened chart tabs must be visible; one or two stable rectangles are treated as loading artifacts. That stable tab geometry maps each tab index to its chart-cell index; it is mapping evidence only. Forms, editable controls, arbitrary attributes and application state are not read.

Explicit **Sync Assets** verifies chart geometry before scanning identities. It reuses a valid manual calibration at the current zoom or resolves the visible chart grid twice and saves the stable automatic geometry. If the grid cannot be verified, Sync stops with a calibration/grid error and preserves every existing identity. Duplicate instruments remain valid in different mapped slots.

For every present tab, OCR must confirm the instrument from the larger title inside the mapped chart cell, with a unique agreement across preprocessing variants. If the narrow top tab produced a usable prefix, the chart title must continue that prefix. A name read only from the top tab is never accepted, even when repeated; missing or competing chart-title evidence leaves the slot UNCERTAIN. A shared OCR worker prevents image queues. Raw OCR text and images are not stored, and arbitrary buttons or account amounts are never treated as instruments.

**Default or legacy rectangles alone are not a verified chart mapping.** Explicit Sync re-resolves an automatic grid from the current surface; a manual grid is reused only at its saved zoom. If the top-tab chain or mapped chart titles cannot be isolated confidently, identities remain uncertain. No asset names are hard-coded.

## Identity and confidence

DOM evidence requires confidence ≥0.9 to apply; the current unambiguous chart-label adapter reports 0.98. OCR requires ≥0.95. These are parser confidence measures, not a guarantee of market identity. Low-confidence or malformed input is UNCERTAIN and preserves the previous configuration.

Canonicalization trims/reduces whitespace, removes spaces around `/`, and treats `(OTC)` as ` OTC`. It preserves the original display label. It never merges OTC and non-OTC instruments, and does not assume `EURUSD` and `EUR/USD` are equivalent. Canonical IDs include platform and normalized asset name.

AUTO mode requires three identical identities from the same source. Transient differences or uncertain attempted reads reset that slot's candidate. Sparse OCR checks count only actual attempts, never cached results. The interval (2–30 seconds) and stability count (2–10) are validated command options; defaults are 3 seconds and 3 checks. An all-OCR nine-slot workspace therefore takes longer to establish stability than a DOM workspace. Auto Sync is off at startup.

## Configuration and history

A sync write atomically compares expected slot configuration and calibration version inside SQLite. If another edit or profile switch occurred during detection, the write is rejected. MANUAL slots are preserved in the storage transaction even if a proposed sync includes replacements. Migration 0003 adds slot/preset modes and locks pre-existing named slots; migrations 0001 and 0002 remain unchanged.

An applied instrument change gives that slot a new market context, clears its queued observations and displayed series state, and prevents old/new instruments from sharing candles. Unchanged slots retain their contexts. Changing calibration or browser geometry invalidates the mapping and all affected capture contexts. The Python builder remains the sole aggregation implementation.

Starting observation with no enabled identity first performs a sync. If identities remain uncertain or calibration is missing, the workspace explains the next action instead of silently starting an empty pipeline. Sync summary reports detected, uncertain, missing, applied and manually preserved slots. Developer diagnostics include source, confidence, evidence, timestamp and detection duration.

## Verification

`npm test` covers adapters, chart geometry, tooltips, OTC identity, ambiguity, locks, debounce, calibrated OCR fallback, default-grid rejection, concurrent edits, presets and per-slot context isolation. `node scripts/market-smoke.mjs --assets` runs both adapters against real Electron DOM with nine generated charts and a conflicting tab label. It detected all nine fixture instruments for each adapter, including the full tooltip for a truncated label. This is fixture evidence, not authenticated broker acceptance.
