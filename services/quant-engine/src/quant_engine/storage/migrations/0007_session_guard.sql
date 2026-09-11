-- Phase 9.5 keeps one settings row for the whole installation: a daily profit target and loss
-- limit belong to the operator, not to one broker workspace, and a session that stopped on one
-- platform while the other kept trading would not be a daily limit at all.
CREATE TABLE IF NOT EXISTS session_guard_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
