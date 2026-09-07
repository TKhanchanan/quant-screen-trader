-- Phase 1 workspace/slot tables remain the sole source of current asset configuration.
CREATE TABLE calibration_profiles (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK(platform IN ('capitalbear', 'iqoption')),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    reference_width INTEGER NOT NULL CHECK(reference_width > 0),
    reference_height INTEGER NOT NULL CHECK(reference_height > 0),
    zoom_factor REAL NOT NULL CHECK(zoom_factor BETWEEN 0.25 AND 5),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE calibration_slots (
    profile_id TEXT NOT NULL REFERENCES calibration_profiles(id) ON DELETE CASCADE,
    slot_number INTEGER NOT NULL CHECK(slot_number BETWEEN 1 AND 9),
    x REAL NOT NULL CHECK(x BETWEEN 0 AND 1),
    y REAL NOT NULL CHECK(y BETWEEN 0 AND 1),
    width REAL NOT NULL CHECK(width > 0 AND x + width <= 1),
    height REAL NOT NULL CHECK(height > 0 AND y + height <= 1),
    PRIMARY KEY(profile_id, slot_number)
);
CREATE TABLE asset_presets (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK(platform IN ('capitalbear', 'iqoption')),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE asset_preset_slots (
    preset_id TEXT NOT NULL REFERENCES asset_presets(id) ON DELETE CASCADE,
    slot_number INTEGER NOT NULL CHECK(slot_number BETWEEN 1 AND 9),
    asset_name TEXT NOT NULL,
    display_name TEXT,
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    CHECK(enabled = 0 OR length(trim(asset_name)) > 0),
    PRIMARY KEY(preset_id, slot_number)
);
CREATE TABLE active_calibrations (
    platform TEXT PRIMARY KEY CHECK(platform IN ('capitalbear', 'iqoption')),
    profile_id TEXT REFERENCES calibration_profiles(id) ON DELETE SET NULL
);
