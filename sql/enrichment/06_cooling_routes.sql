CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.truck_travel_times` AS
SELECT * FROM `{{PROJECT}}.{{WORK}}.cooling_routes` WHERE mode = 'truck';

ASSERT (SELECT COUNT(*) = COUNT(DISTINCT route_id) FROM `{{PROJECT}}.{{WORK}}.cooling_routes`)
  AS 'One route result per event/cell/mode';
ASSERT (SELECT COUNT(*) = 0 FROM `{{PROJECT}}.{{WORK}}.cooling_routes`
  WHERE traffic_data_used OR route_operationally_verified OR estimated_truck_minutes IS NOT NULL)
  AS 'Static network routes cannot imply traffic time or operational verification';
ASSERT (SELECT COUNT(*) = 0 FROM `{{PROJECT}}.{{WORK}}.cooling_routes`
  WHERE route_status = 'preliminary_mapped_route' AND
    (network_distance_m IS NULL OR origin_connector_m > 150 OR destination_connector_m > 150))
  AS 'Mapped routes require an actual network path and bounded unverified connectors';

SELECT mode, COUNT(*) AS route_results, COUNTIF(route_status = 'preliminary_mapped_route') AS mapped_routes,
  MIN(network_distance_m) AS min_network_m, MAX(network_distance_m) AS max_network_m,
  MIN(estimated_walk_minutes) AS min_assumed_walk_minutes,
  MAX(estimated_walk_minutes) AS max_assumed_walk_minutes,
  COUNTIF(estimated_truck_minutes IS NOT NULL) AS available_truck_durations
FROM `{{PROJECT}}.{{WORK}}.cooling_routes` GROUP BY mode ORDER BY mode;
