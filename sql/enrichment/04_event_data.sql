-- Histories retain all prepared runs. A validity row commits each run after
-- its other tables load. Empty/out-of-coverage refreshes never resurrect old rows.
CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.event_data_validity` AS
SELECT * FROM `{{PROJECT}}.{{WORK}}.event_data_validity_history`
QUALIFY ROW_NUMBER() OVER (PARTITION BY event_date ORDER BY prepared_at DESC) = 1;

CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.event_transit_departures` AS
SELECT DISTINCT h.* FROM `{{PROJECT}}.{{WORK}}.event_transit_departures_history` h
JOIN `{{PROJECT}}.{{WORK}}.event_data_validity` v USING (event_date, prepared_at);
CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.event_transit_validity` AS
SELECT DISTINCT h.* FROM `{{PROJECT}}.{{WORK}}.event_transit_validity_history` h
JOIN `{{PROJECT}}.{{WORK}}.event_data_validity` v USING (event_date, prepared_at);
CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.event_weather_hourly` AS
SELECT DISTINCT h.* FROM `{{PROJECT}}.{{WORK}}.event_weather_hourly_history` h
JOIN `{{PROJECT}}.{{WORK}}.event_data_validity` v USING (event_date, prepared_at);
CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.event_weather_summary` AS
SELECT DISTINCT h.* FROM `{{PROJECT}}.{{WORK}}.event_weather_summary_history` h
JOIN `{{PROJECT}}.{{WORK}}.event_data_validity` v USING (event_date, prepared_at);
CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.weather_event` AS
SELECT * FROM `{{PROJECT}}.{{WORK}}.event_weather_summary`;
CREATE OR REPLACE VIEW `{{PROJECT}}.{{WORK}}.transit_departures_event` AS
SELECT * FROM `{{PROJECT}}.{{WORK}}.event_transit_departures`;

ASSERT (SELECT COUNT(*) = 0 FROM `{{PROJECT}}.{{WORK}}.event_transit_departures`
  WHERE event_date != actual_local_date) AS 'Transit clock date must match the event date';
ASSERT (SELECT COUNT(*) = 0 FROM `{{PROJECT}}.{{WORK}}.event_weather_summary`
  WHERE (NOT civil_day_complete AND forecast_high_f IS NOT NULL) OR
        (NOT daytime_complete AND daytime_high_f IS NOT NULL) OR
        (NOT overnight_complete AND overnight_low_f IS NOT NULL))
  AS 'Incomplete forecast windows must retain unavailable extremes';
ASSERT (SELECT COUNT(*) = 0 FROM (
  SELECT event_date, grid_key, COUNT(*) n FROM `{{PROJECT}}.{{WORK}}.event_weather_summary`
  GROUP BY event_date, grid_key HAVING n > 1)) AS 'Weather summary must have one row per event/grid';

SELECT '{{EVENT_DATE}}' AS event_date,
  (SELECT COUNT(*) FROM `{{PROJECT}}.{{WORK}}.event_transit_departures`
   WHERE event_date = DATE('{{EVENT_DATE}}')) AS transit_aggregate_rows,
  (SELECT COUNTIF(is_previous_service_date) FROM `{{PROJECT}}.{{WORK}}.event_transit_departures`
   WHERE event_date = DATE('{{EVENT_DATE}}')) AS previous_service_date_spillover_rows,
  (SELECT SUM(scheduled_departures) FROM `{{PROJECT}}.{{WORK}}.event_transit_departures`
   WHERE event_date = DATE('{{EVENT_DATE}}')) AS scheduled_stop_departures,
  (SELECT COUNT(*) FROM `{{PROJECT}}.{{WORK}}.event_weather_summary`
   WHERE event_date = DATE('{{EVENT_DATE}}')) AS forecast_grids,
  (SELECT COUNTIF(civil_day_complete) FROM `{{PROJECT}}.{{WORK}}.event_weather_summary`
   WHERE event_date = DATE('{{EVENT_DATE}}')) AS complete_civil_day_forecasts,
  (SELECT COUNTIF(overnight_complete) FROM `{{PROJECT}}.{{WORK}}.event_weather_summary`
   WHERE event_date = DATE('{{EVENT_DATE}}')) AS complete_overnight_forecasts;
