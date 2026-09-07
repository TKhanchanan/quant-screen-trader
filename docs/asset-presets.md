# Asset slots and presets

Each platform has exactly nine slots numbered 1–9. Users enter asset names freely; there is no hard-coded global instrument list. Enabled slots require a non-empty asset name. Optional display names label the toolbar; calibration shows the actual configured asset name. Disabling a slot retains its asset/display configuration.

Asset Setup edits a local draft. Save assets writes all nine slots atomically. Cancel/Close leaves persisted assets unchanged. The application does not select instruments on the platform, submit orders, or analyze enabled slots in this phase.

## Preset actions

| Action | Result |
| --- | --- |
| Create preset | Save the current nine-slot draft as a new named preset. Does not apply it to current assets. |
| Update preset | Replace the selected preset with the current draft and entered name. |
| Rename | Change the selected preset's name while retaining its stored slots. |
| Duplicate | Copy the selected preset's stored slots under the entered name and a new ID. |
| Load preset | Replace and persist current asset assignments with the selected preset; also replace the open form draft. |
| Delete | Require confirmation, then remove only the selected preset and its child rows. Current assets are unchanged. |

Loading is explicitly labeled as replacement. Preset names need not be unique; IDs distinguish records. Presets contain asset assignments and enabled flags, not calibration geometry. CapitalBear and IQ Option records cannot be read, loaded, updated or deleted through the other workspace's IPC/API scope.

Configuration lives in the local engine's SQLite database, not localStorage or source files. Offline/save errors are displayed and preserve the editor draft. Restore engine health and retry; no silent fallback file is written.
