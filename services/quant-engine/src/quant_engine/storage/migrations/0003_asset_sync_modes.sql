-- Explicit manual locks survive preset operations. Existing named slots stay protected.
ALTER TABLE slot_profiles ADD COLUMN asset_mode TEXT NOT NULL DEFAULT 'AUTO' CHECK(asset_mode IN ('AUTO','MANUAL'));
UPDATE slot_profiles SET asset_mode='MANUAL' WHERE length(trim(asset_name)) > 0;
ALTER TABLE asset_preset_slots ADD COLUMN asset_mode TEXT NOT NULL DEFAULT 'AUTO' CHECK(asset_mode IN ('AUTO','MANUAL'));
UPDATE asset_preset_slots SET asset_mode='MANUAL' WHERE length(trim(asset_name)) > 0;
