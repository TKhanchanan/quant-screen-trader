CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workspace_profiles (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL CHECK (platform IN ('capitalbear', 'iqoption')),
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (platform, name)
);

CREATE TABLE slot_profiles (
    id INTEGER PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspace_profiles(id) ON DELETE CASCADE,
    slot_number INTEGER NOT NULL CHECK (slot_number BETWEEN 1 AND 9),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    asset_name TEXT NOT NULL DEFAULT '',
    display_name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, slot_number)
);

CREATE TABLE health_events (
    id INTEGER PRIMARY KEY,
    service TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX health_events_created_at_idx ON health_events (created_at);
