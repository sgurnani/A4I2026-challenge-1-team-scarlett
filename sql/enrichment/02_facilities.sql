-- Refreshable inventories and operator-owned confirmations have separate tables.
CREATE TABLE IF NOT EXISTS `{{PROJECT}}.{{WORK}}.facility_readiness` (
  facility_id STRING, event_date DATE, readiness_status STRING, open_for_event BOOL,
  available_capacity INT64, working_ac_confirmed BOOL, staffing_confirmed BOOL,
  wheelchair_accessible_confirmed BOOL, verified_by STRING,
  readiness_verified_at TIMESTAMP, verification_source STRING, retrieved_at TIMESTAMP
);
INSERT INTO `{{PROJECT}}.{{WORK}}.facility_readiness`
SELECT s.* FROM `{{PROJECT}}.{{WORK}}.facility_readiness_seed` s
WHERE NOT EXISTS (SELECT 1 FROM `{{PROJECT}}.{{WORK}}.facility_readiness` r
  WHERE r.facility_id = s.facility_id AND r.event_date IS NULL);

CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.facility_crosswalk` AS
SELECT * FROM `{{PROJECT}}.{{WORK}}.canonical_facilities`;

-- Compatibility interface for the new toolset. is_indoor means an inventory
-- category lists an indoor site; it is never an operator AC/open confirmation.
CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.cooling_facilities` AS
WITH details AS (
  SELECT facility_id,
    COALESCE(JSON_VALUE(source_row_json, '$.hours_of_operation'),
             JSON_VALUE(source_row_json, '$.hours'), JSON_VALUE(source_row_json, '$.service_hours')) AS hours,
    COALESCE(JSON_VALUE(source_row_json, '$.phone'), JSON_VALUE(source_row_json, '$.telephone')) AS phone
  FROM `{{PROJECT}}.{{WORK}}.facility_source_crosswalk`
  WHERE source_table IN ('cooling_inventory_snapshot', 'chi_site_libraries', 'chi_site_cooling_centers')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY facility_id ORDER BY
    CASE source_table WHEN 'cooling_inventory_snapshot' THEN 0 WHEN 'chi_site_libraries' THEN 1 ELSE 2 END,
    source_record_id) = 1
)
SELECT f.facility_id, f.facility_name AS site_name,
  CASE f.facility_category WHEN 'library' THEN 'Library'
    WHEN 'high_school' THEN 'High School' WHEN 'park_facility' THEN 'Park District Facility'
    WHEN 'spray_feature' THEN 'Park District Spray Feature'
    ELSE INITCAP(REPLACE(f.facility_category, '_', ' ')) END AS facility_type,
  f.address, f.zip, d.phone, d.hours AS hours_of_operation, f.latitude, f.longitude,
  f.indoor_cooling_classification = 'listed_unconfirmed' AS is_indoor,
  f.in_cooling_inventory AS is_designated_inventory_site,
  f.indoor_cooling_classification, 'unconfirmed' AS event_readiness_status,
  f.coordinate_source_table, f.has_source_coordinate_conflict
FROM `{{PROJECT}}.{{WORK}}.canonical_facilities` f
LEFT JOIN details d USING (facility_id);

CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.chi_site_candidate_sites` AS
SELECT f.facility_id AS site_key, f.facility_name AS site_name,
  c.facility_type AS site_category, CAST(NULL AS STRING) AS operator,
  f.address, f.latitude, f.longitude,
  f.in_cooling_inventory AS is_current_cooling_site,
  CAST(NULL AS BOOL) AS indoor_air_conditioned, CAST(NULL AS BOOL) AS open_24_7,
  c.hours_of_operation,
  f.is_indoor_inventory_access_candidate AS is_indoor_inventory_listing,
  f.candidate_status, f.identity_review_required,
  f.has_source_coordinate_conflict, 'unconfirmed' AS readiness_status
FROM `{{PROJECT}}.{{WORK}}.canonical_facilities` f
JOIN `{{PROJECT}}.{{WORK}}.cooling_facilities` c USING (facility_id)
WHERE f.in_cooling_inventory OR f.is_current_high_school_candidate OR f.is_legacy_high_school;

ASSERT (SELECT COUNT(*) = COUNT(DISTINCT facility_id)
  FROM `{{PROJECT}}.{{WORK}}.canonical_facilities`) AS 'Canonical facilities must be unique';
ASSERT (SELECT COUNT(*) = 187 AND COUNT(DISTINCT facility_id) = 187
  FROM `{{PROJECT}}.{{WORK}}.facility_source_crosswalk` WHERE source_table = 'high_schools')
  AS 'Every legacy school must remain individually recoverable in the crosswalk';
ASSERT (SELECT COUNT(*) = 0 FROM `{{PROJECT}}.{{WORK}}.facility_source_crosswalk` x
  LEFT JOIN `{{PROJECT}}.{{WORK}}.canonical_facilities` f USING (facility_id)
  WHERE f.facility_id IS NULL) AS 'Crosswalk may not contain orphan source rows';
ASSERT (SELECT COUNT(*) = 0 FROM `{{PROJECT}}.{{WORK}}.canonical_facilities`
  WHERE facility_category IN ('park_facility', 'high_school', 'school')
    AND indoor_cooling_classification != 'unconfirmed') AS 'Unverified schools and parks cannot imply indoor cooling';

SELECT COUNT(*) AS canonical_facilities,
  COUNTIF(is_legacy_high_school) AS legacy_schools_preserved,
  COUNTIF(is_current_cps_high_school) AS current_cps_high_schools,
  COUNTIF(in_cooling_inventory) AS inventory_sites,
  COUNTIF(indoor_cooling_classification = 'listed_unconfirmed') AS listed_indoor_sites,
  COUNTIF(has_source_coordinate_conflict) AS facilities_with_coordinate_disagreement,
  (SELECT COUNTIF(readiness_status = 'unconfirmed') FROM `{{PROJECT}}.{{WORK}}.facility_readiness`) AS unconfirmed_readiness_rows
FROM `{{PROJECT}}.{{WORK}}.canonical_facilities`;
