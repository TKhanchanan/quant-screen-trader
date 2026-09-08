# Automatic asset synchronization

Use **Sync Assets** to inspect the displayed instruments and apply confident identities to AUTO slots. **Auto Sync Assets** repeats the check every 3 seconds by default and requires three consistent, high-confidence checks before changing an asset. Manual typing remains available in Asset Setup. Editing an asset or its enabled checkbox locks that slot to MANUAL; switch its mode back to AUTO to allow synchronization. Presets save and restore these modes.

## Detection and mapping

CapitalBear and IQ Option have separate adapter classes with centralized selectors. They inspect visible instrument labels within visible chart containers containing a canvas/SVG. Global instrument tabs, forms and editable controls are excluded. A complete matching label tooltip may recover a visibly truncated name; arbitrary attributes and application state are not read.

Mapping uses the chart's location in a calibrated region. Without calibration, DOM detection requires a complete nine-container, three-row/three-column chart layout. Missing/ambiguous mapping is not replaced by tab order. Duplicate instruments are valid in different mapped slots. Two competing chart labels in the same slot remain uncertain.

When DOM is insufficient, OCR reads only the top label strip of a calibrated chart: at most the top 16% or 48 device-independent pixels, normalized using the existing native capture pipeline. Auto mode attempts one missing label per cycle; explicit Sync visits missing labels sequentially. A shared asset OCR worker prevents image queues. Raw OCR text and images are not stored. Instrument-name OCR requires an unambiguous currency-pair or OTC label; it does not treat arbitrary buttons or account amounts as instruments.

**The default full-browser 3×3 calibration is not a verified chart mapping.** OCR refuses unchanged default regions, because they may include the broker toolbar or instrument tabs. Align and save the regions to individual charts first. The two user-opened broker UIs inspected during this continuation exposed a single canvas and no DOM instrument labels; their saved profiles were still the default whole-browser grid. Therefore live detection has not yet been established for those layouts. No asset names have been hard-coded.

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
