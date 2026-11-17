-- One row per unchanged grid cell. Percent cover remains explicitly percent;
-- satellite surface offsets and measured air offsets remain separate quantities.
CREATE OR REPLACE TABLE `{{PROJECT}}.{{WORK}}.site_context_enriched` AS
WITH land AS (
  SELECT * FROM `{{PROJECT}}.{{SOURCE}}.cell_land_cover`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY cell_id ORDER BY computed_at DESC) = 1
), surface AS (
  SELECT * FROM `{{PROJECT}}.{{SOURCE}}.cell_surface_temp`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY cell_id ORDER BY n_scenes DESC, last_year DESC) = 1
), housing AS (
  SELECT * FROM `{{PROJECT}}.{{SOURCE}}.tract_housing_burden`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY geo_id ORDER BY acs_year DESC, retrieved_at DESC) = 1
), canopy AS (
  SELECT * FROM `{{PROJECT}}.{{SOURCE}}.tract_tree_canopy`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY geo_id ORDER BY canopy_imagery_year DESC, retrieved_at DESC) = 1
), cells AS (
  SELECT cell_id, ST_GEOGPOINT(longitude, latitude) AS pt
  FROM `{{PROJECT}}.{{SOURCE}}.grid_cells`
), nearest_cool AS (
  SELECT c.cell_id, f.facility_id, f.site_name, f.facility_type, f.address, f.hours_of_operation,
    ST_DISTANCE(c.pt, ST_GEOGPOINT(f.longitude, f.latitude)) / 1000 AS km
  FROM cells c CROSS JOIN `{{PROJECT}}.{{WORK}}.cooling_facilities` f
  WHERE f.is_indoor AND f.latitude IS NOT NULL AND f.longitude IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY cell_id ORDER BY km, facility_id) = 1
), nearest_lib AS (
  SELECT c.cell_id, f.facility_id, f.site_name, f.address, f.hours_of_operation,
    ST_DISTANCE(c.pt, ST_GEOGPOINT(f.longitude, f.latitude)) / 1000 AS km
  FROM cells c CROSS JOIN `{{PROJECT}}.{{WORK}}.cooling_facilities` f
  WHERE f.facility_type = 'Library' AND f.latitude IS NOT NULL AND f.longitude IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY cell_id ORDER BY km, facility_id) = 1
), nearest_school AS (
  SELECT c.cell_id, f.facility_id, f.facility_name, f.address,
    ST_DISTANCE(c.pt, ST_GEOGPOINT(f.longitude, f.latitude)) / 1000 AS km
  FROM cells c CROSS JOIN `{{PROJECT}}.{{WORK}}.canonical_facilities` f
  WHERE f.is_current_high_school_candidate AND NOT f.identity_review_required
    AND f.latitude IS NOT NULL AND f.longitude IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY cell_id ORDER BY km, facility_id) = 1
)
SELECT g.* EXCEPT (tract_geo_id), m.tract_geo_id, m.original_tract_geo_id,
  m.mapping_status, m.tract_changed, m.inside_census_chicago_place, m.boundary_year,
  v.total_pop AS tract_pop, v.total_population_moe AS tract_population_moe,
  v.pop_65_plus, v.population_65_plus_moe, v.pct_65_plus, v.households, v.households_moe,
  v.no_car_households, v.no_vehicle_households_moe, v.no_vehicle_rate, v.poverty_rate,
  v.population_below_poverty, v.population_below_poverty_moe, v.poverty_universe_population,
  v.poverty_universe_population_moe, v.median_income, v.median_household_income_moe,
  v.median_age, v.median_age_moe, v.vulnerability_index,
  v.older_adults_living_alone, v.older_adults_living_alone_moe, v.older_adults_living_alone_rate,
  v.people_with_disability, v.people_with_disability_moe, v.disability_rate,
  v.children_under_5, v.children_under_5_moe, v.children_under_5_rate,
  v.acs_year, v.estimate_period, v.ranking_geographic_scope,
  v.vulnerability_rate_missing, v.ranking_uses_median_imputation,
  l.tree_canopy_pct, l.impervious_pct, l.tree_canopy_pct_300m, l.impervious_pct_300m,
  l.vintage AS land_cover_year, l.source_canopy, l.source_impervious,
  s.lst_offset_f, s.lst_offset_iqr_f, s.lst_offset_q25_f, s.lst_offset_q75_f,
  s.n_scenes, s.first_year AS surface_first_year, s.last_year AS surface_last_year,
  s.lst_offset_f IS NULL AS surface_offset_missing,
  COALESCE(s.n_scenes, 0) < 5 AS surface_low_scene_count,
  'Morning Landsat land-surface offset, degrees F; not an air-temperature offset' AS surface_quantity,
  h.rent_burden_30_plus_rate, h.rent_burden_50_plus_rate, h.computed_rent_burden_units,
  h.rent_burden_30_plus_units, h.rent_burden_30_plus_units_moe, h.acs_year AS housing_acs_year,
  t.tree_canopy_percent AS comparison_tract_canopy_pct,
  t.canopy_imagery_year AS comparison_tract_canopy_year, t.reuse_terms_status AS comparison_canopy_reuse_terms,
  v.geo_id IS NULL AS census_missing,
  h.geo_id IS NULL OR h.rent_burden_30_plus_rate IS NULL AS housing_burden_missing,
  nc.facility_id AS nearest_cooling_facility_id, nc.site_name AS nearest_cooling_name,
  nc.facility_type AS nearest_cooling_type, nc.address AS nearest_cooling_address,
  nc.hours_of_operation AS nearest_cooling_hours, nc.km AS km_to_cooling,
  'Listed indoor inventory; event opening and working AC unconfirmed' AS nearest_cooling_status,
  FALSE AS nearest_cooling_event_verified,
  nl.facility_id AS nearest_library_facility_id, nl.site_name AS nearest_library_name,
  nl.address AS nearest_library_address, nl.hours_of_operation AS nearest_library_hours,
  nl.km AS km_to_library,
  hs.facility_id AS nearest_high_school_facility_id, hs.facility_name AS nearest_high_school_name,
  hs.address AS nearest_high_school_address, hs.km AS km_to_high_school,
  CAST(NULL AS STRING) AS nearest_high_school_hours,
  'Candidate from imported CPS2025-26 snapshot; event opening, AC and public access unconfirmed' AS nearest_high_school_status,
  'Straight-line distance, not a walking or driving travel time' AS proximity_method,
  '{{PROJECT}}.{{SOURCE}}.grid_cells' AS physical_grid_source,
  CURRENT_TIMESTAMP() AS enriched_at
FROM `{{PROJECT}}.{{SOURCE}}.grid_cells` g
LEFT JOIN `{{PROJECT}}.{{WORK}}.cell_tract_map` m USING (cell_id)
LEFT JOIN `{{PROJECT}}.{{WORK}}.tract_vulnerability_2024` v ON v.geo_id = m.tract_geo_id
LEFT JOIN land l USING (cell_id)
LEFT JOIN surface s USING (cell_id)
LEFT JOIN housing h ON h.geo_id = m.tract_geo_id
LEFT JOIN canopy t ON t.geo_id = m.tract_geo_id
LEFT JOIN nearest_cool nc USING (cell_id)
LEFT JOIN nearest_lib nl USING (cell_id)
LEFT JOIN nearest_school hs USING (cell_id);

ASSERT (SELECT COUNT(*) = COUNT(DISTINCT cell_id) FROM `{{PROJECT}}.{{WORK}}.site_context_enriched`)
  AS 'Enriched context must be unique by cell';
ASSERT (SELECT COUNT(*) FROM `{{PROJECT}}.{{WORK}}.site_context_enriched`) =
  (SELECT COUNT(*) FROM `{{PROJECT}}.{{SOURCE}}.grid_cells`) AS 'No cell may be lost';
ASSERT (SELECT COUNTIF(census_missing) = 0 FROM `{{PROJECT}}.{{WORK}}.site_context_enriched`)
  AS 'Every cell needs its aligned Census row';
ASSERT (SELECT COUNTIF(tree_canopy_pct IS NULL OR impervious_pct IS NULL OR
  tree_canopy_pct NOT BETWEEN 0 AND 100 OR impervious_pct NOT BETWEEN 0 AND 100) = 0
  FROM `{{PROJECT}}.{{WORK}}.site_context_enriched`) AS 'Primary land cover must use valid percent units';
ASSERT (SELECT COUNT(*) = 0 FROM `{{PROJECT}}.{{WORK}}.site_context_enriched` e
  JOIN `{{PROJECT}}.{{SOURCE}}.grid_cells` g USING (cell_id)
  WHERE e.offset_f IS DISTINCT FROM g.offset_f OR e.is_holdout IS DISTINCT FROM g.is_holdout
    OR e.n_obs IS DISTINCT FROM g.n_obs) AS 'Measured labels and splits must be unchanged';

SELECT COUNT(*) AS cells, COUNT(DISTINCT cell_id) AS unique_cells,
  COUNTIF(surface_offset_missing) AS cells_missing_surface_offset,
  COUNTIF(surface_low_scene_count) AS cells_with_fewer_than_five_scenes,
  COUNTIF(housing_burden_missing) AS cells_without_housing_burden,
  COUNTIF(vulnerability_index IS NULL) AS cells_without_vulnerability_rank,
  COUNTIF(km_to_cooling IS NULL) AS cells_without_listed_indoor_proximity,
  COUNTIF(NOT inside_census_chicago_place) AS city_boundary_disagreements
FROM `{{PROJECT}}.{{WORK}}.site_context_enriched`;
