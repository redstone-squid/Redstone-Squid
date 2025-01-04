DELETE
FROM versions
WHERE edition IS NULL
  AND major_version IS NULL
  AND minor_version IS NULL
  AND patch_number IS NULL
  AND full_name_temp IS NOT NULL;

ALTER TABLE versions ALTER COLUMN edition SET NOT NULL;
ALTER TABLE versions ALTER COLUMN major_version SET NOT NULL;
ALTER TABLE versions ALTER COLUMN minor_version SET NOT NULL;
ALTER TABLE versions ALTER COLUMN patch_number SET NOT NULL;
ALTER TABLE versions DROP COLUMN full_name_temp;