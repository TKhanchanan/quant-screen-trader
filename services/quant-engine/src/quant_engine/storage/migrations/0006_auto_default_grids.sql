-- A profile still holding the untouched derived 3x3 default was never hand-adjusted, so it must
-- not be treated as a manual calibration: that pinned it to the zoom it was created at and blocked
-- automatic regeneration for the current browser state.
UPDATE calibration_profiles SET geometry_source = 'AUTO' WHERE id IN (
    SELECT profile_id
    FROM calibration_slots
    GROUP BY profile_id
    HAVING COUNT(*) = 9
       AND SUM(
           CASE WHEN
               ABS(x - (0.05 + ((slot_number - 1) % 3) * (0.95 / 3.0))) < 0.000001 AND
               ABS(y - (0.12 + CAST((slot_number - 1) / 3 AS INTEGER) * (0.78 / 3.0))) < 0.000001 AND
               ABS(width - (0.95 / 3.0)) < 0.000001 AND
               ABS(height - (0.78 / 3.0)) < 0.000001
           THEN 1 ELSE 0 END
       ) = 9
);
