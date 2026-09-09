ALTER TABLE calibration_profiles ADD COLUMN geometry_source TEXT NOT NULL DEFAULT 'MANUAL'
    CHECK (geometry_source IN ('AUTO', 'MANUAL'));
UPDATE calibration_profiles SET geometry_source = 'AUTO' WHERE name = 'Auto Chart Grid';
UPDATE slot_profiles SET asset_name = '', display_name = NULL, enabled = 0
    WHERE asset_mode = 'AUTO' AND asset_name = 'Unassigned';
