-- Convert only the original full-browser 3x3 default. User-adjusted profiles remain unchanged.
UPDATE calibration_slots
SET x = 0.05 + ((slot_number - 1) % 3) * (0.95 / 3.0),
    y = 0.12 + CAST((slot_number - 1) / 3 AS INTEGER) * (0.78 / 3.0),
    width = 0.95 / 3.0,
    height = 0.78 / 3.0
WHERE profile_id IN (
    SELECT profile_id
    FROM calibration_slots
    GROUP BY profile_id
    HAVING COUNT(*) = 9
       AND SUM(
           CASE WHEN
               ABS(x - (((slot_number - 1) % 3) / 3.0)) < 0.00000001 AND
               ABS(y - (CAST((slot_number - 1) / 3 AS INTEGER) / 3.0)) < 0.00000001 AND
               ABS(width - (1.0 / 3.0)) < 0.00000001 AND
               ABS(height - (1.0 / 3.0)) < 0.00000001
           THEN 1 ELSE 0 END
       ) = 9
);
