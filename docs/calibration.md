# Slot calibration

Calibration labels nine user-selected chart regions. It does not capture charts, parse pixels or generate signals.

## Workflow

1. Configure asset names in Asset Setup and select the corresponding instruments manually on the platform.
2. Click Calibrate Slots. A transparent local overlay appears over the live browser content region; platform interaction is intentionally intercepted while editing.
3. Move a box by its labeled handle; resize with its bottom-right handle. Arrow keys move a focused label, and Shift+arrows resize it. The selected box is raised above overlaps; the Selected slot control makes obscured boxes accessible. Fill opacity is adjustable.
4. Enter a name and Create profile, or Save calibration to replace the selected profile. Save errors leave the draft open for retry. Cancel/Close discards unsaved geometry.
5. Load a saved profile, rename it, duplicate it, or explicitly confirm deletion. Reset 3×3 changes only the draft until saved.

Selecting a profile in the dropdown does not load it until Load profile is clicked. Loading and saving also persist the active profile selection. Rename preserves the stored geometry; Duplicate copies the stored profile. Create profile saves the current draft to a new ID. Asset assignments and geometry are separate: loading a preset never changes bounds and loading calibration never changes asset labels.

## Coordinates and validation

`NormalizedBoundsSchema` is the canonical TypeScript bounds contract; the Python API mirrors these constraints. For each box, x/y are within 0–1, width/height are positive, and x+width/y+height cannot exceed 1. Profiles must contain exactly IDs 1–9 with no duplicates. Invalid records are rejected atomically before persistence. The overlay marks each valid slot VALID; invalid external profiles cannot be loaded. Overlap is allowed and does not invalidate a profile.

The deterministic default grid uses row/column positions divided by 3 and dimensions of 1/3. `normalizedToPixel` multiplies x/width by browser content width, and y/height by browser content height. The toolbar, OS window frame and desktop coordinates are excluded. CSS percentages implement the same conversion in the overlay.

Resize changes native browser and overlay bounds together, preserving normalized geometry across window sizes and aspect ratios. Reference browser width/height and zoom factor are stored as profile metadata, not used as absolute screen coordinates. Profiles are scoped to one platform and survive app restarts in SQLite. Create separate named profiles for monitors/layouts when useful.

## Limitations

Proportional geometry does not infer how a platform rearranges its own responsive charts. If the platform changes layout or chart zoom, review calibration. Browser zoom is kept fixed by the application while using a profile. Profiles currently use the default factor of 1 from the UI; the validated schema can retain an explicit factor for an existing profile.

No chart screenshots or account data are saved by calibration. The overlay and its draft are temporary; only explicitly saved normalized profiles are durable. Stake configuration is deferred to Phase 9; no trading controls are added here.
