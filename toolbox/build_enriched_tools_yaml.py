#!/usr/bin/env python3
"""Generate an opt-in Toolbox toolset using validated enrichment outputs.

The existing tools.yaml and deployed toolset remain available. The enriched
toolset uses the frozen original model with unchanged physical inputs until a
candidate has been reviewed. Its data layer uses corrected ACS2024 geography.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

import build_tools_yaml as legacy

HERE = Path(__file__).resolve().parent
OUT = HERE / "tools.enriched.yaml"
WORK = "${GOOGLE_CLOUD_PROJECT}.${BQ_ENRICHED_DATASET:a4i_climate_work_v2}"
SOURCE = "${GOOGLE_CLOUD_PROJECT}.${BQ_BASELINE_DATASET:a4i_climate_backup_20261117_214035}"
OLD = "${GOOGLE_CLOUD_PROJECT}.${BQ_DATASET:a4i_climate}"
WORK_TABLES = {"site_context": "site_context_enriched", "tract_vulnerability": "tract_vulnerability_2024",
               "cooling_facilities": "cooling_facilities", "chi_site_candidate_sites": "chi_site_candidate_sites"}


def build() -> list[dict]:
    docs = legacy.build()
    for document in docs:
        if document["kind"] == "toolset":
            document["name"] = "heat_dispatch_enriched"
            continue
        if document["kind"] != "tool":
            continue
        statement = document["statement"]
        for old_name, new_name in WORK_TABLES.items():
            statement = statement.replace(f"`{OLD}.{old_name}`", f"`{WORK}.{new_name}`")
        statement = statement.replace(f"`{OLD}.", f"`{SOURCE}.")
        # Dispatch candidates stay inside matching-vintage Census Chicago;
        # all cells remain present in the context and model evaluation tables.
        statement = statement.replace("WHERE pop_density_km2 >= @min_density",
                                      "WHERE inside_census_chicago_place AND vulnerability_index IS NOT NULL "
                                      "AND pop_density_km2 >= @min_density")
        # Rates use their actual household/poverty universes, not population
        # weighting of household shares or the wrong poverty denominator.
        statement = statement.replace("SUM(no_vehicle_rate * total_pop), SUM(total_pop)",
                                      "SUM(no_car_households), SUM(households)")
        statement = statement.replace("SUM(poverty_rate * total_pop), SUM(total_pop)",
                                      "SUM(population_below_poverty), SUM(poverty_universe_population)")
        if document["name"] in {"rank_priority_sites", "predict_site_heat"}:
            statement = statement.replace("n_obs AS measured_readings,",
                "acs_year, tree_canopy_pct, impervious_pct, lst_offset_f AS surface_offset_f, n_scenes,\n"
                "  surface_offset_missing, surface_low_scene_count, nearest_cooling_status,\n"
                "  nearest_cooling_event_verified, nearest_cooling_facility_id, nearest_library_facility_id,\n"
                "  original_tract_geo_id, tract_changed,\n"
                "  nearest_high_school_name, nearest_high_school_address, nearest_high_school_hours,\n"
                "  nearest_high_school_status, km_to_high_school,\n"
                "  n_obs AS measured_readings,")
        if document["name"] == "tract_map_layer":
            statement = statement.replace("WHERE is_current_cooling_site AND indoor_air_conditioned",
                                          "WHERE is_current_cooling_site AND is_indoor_inventory_listing")
            statement = statement.replace(
                "SELECT geo_id, tract_geom\n  FROM `bigquery-public-data.geo_census_tracts.census_tracts_illinois`",
                f"SELECT geo_id, ST_GEOGFROMGEOJSON(geojson, make_valid => TRUE) AS tract_geom\n"
                f"  FROM `{WORK}.tract_boundaries_2024`")
        if document["name"] == "list_sites":
            statement = statement.replace("s.is_current_cooling_site,", "s.readiness_status, s.candidate_status, "
                                          "s.identity_review_required, s.has_source_coordinate_conflict, "
                                          "s.is_indoor_inventory_listing, s.is_current_cooling_site,")
        if document["name"] == "nearest_cooling_facilities":
            statement = statement.replace("  facility_type, site_name,", "  facility_id, event_readiness_status, "
                                          "coordinate_source_table, has_source_coordinate_conflict, facility_type, site_name,")
        document["statement"] = statement
        document["description"] = (
            "Uses validated enriched context and ACS2024 whole-tract estimates. The air-offset model is the frozen "
            "original model; land-cover and surface-offset fields are additional context. Cooling listings and hours "
            "are source inventories; event opening, working AC, staffing, capacity and accessibility are unconfirmed. "
            "School snapshot membership does not establish current operation or eligibility. "
            "Distances are straight-line unless a route is explicitly supplied.\n" + document["description"]
        )
        if document["name"] in {"nearest_cooling_facilities", "list_sites", "tract_map_layer"}:
            document["description"] = (
                "Returns reconciled inventory candidates with source provenance. Listed indoor sites are not "
                "operator-verified cooling destinations. Schools and parks retain unknown AC and public access. "
                "Confirm event readiness before dispatching residents. Source periods include legacy schools "
                "2013–2014, imported CPS 2025–2026 and cooling inventory Summer 2024.\n"
            )
    event_date = {"name": "event_date", "type": "string",
                  "description": "Explicit planning date in YYYY-MM-DD format; absent dates have no prepared snapshot."}
    additions = [legacy.tool(
        "get_event_weather",
        "Forecast summaries for the explicit event date. Reports full-day/daytime/overnight coverage, missing hours "
        "and source/reference freshness for six NWS grids. Incomplete-window highs/lows are null. "
        "These are forecasts, not historical campaign observations. Zero rows means unavailable event data.",
        f"SELECT *, TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), source_retrieved_at, SECOND) / 3600.0 AS source_age_now_hours,\n"
        f" source_retrieved_at IS NULL OR TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), source_retrieved_at, SECOND) > 21600 "
        f"AS refresh_required_now\nFROM `{WORK}.event_weather_summary` WHERE event_date = DATE(@event_date) ORDER BY grid_key",
        [event_date]),
        legacy.tool(
        "get_event_transit",
        "Scheduled boardable CTA departures near a point for the explicit event clock date and hour window. "
        "Includes prior service-date spillover and preserves schedule provenance. It does not establish a cooling "
        "itinerary, live arrival, disruption status or wheelchair-accessible trip. Zero rows means no prepared matching schedule.",
        f"SELECT d.event_date, d.actual_local_hour, d.stop_id, s.stop_name, d.route_id,\n"
        f" SUM(d.scheduled_departures) AS scheduled_stop_departures, MIN(d.first_departure_utc) AS first_departure_utc,\n"
        f" MAX(d.last_departure_utc) AS last_departure_utc, ANY_VALUE(d.data_kind) AS data_kind,\n"
        f" ANY_VALUE(d.source_retrieved_at) AS source_retrieved_at, ANY_VALUE(s.wheelchair_boarding) AS source_wheelchair_boarding,\n"
        f" ANY_VALUE(ST_DISTANCE(ST_GEOGPOINT(s.longitude, s.latitude), ST_GEOGPOINT(@longitude,@latitude))) AS straight_line_m\n"
        f"FROM `{WORK}.event_transit_departures` d JOIN `{SOURCE}.transit_stops` s USING(stop_id)\n"
        f"WHERE d.event_date = DATE(@event_date) AND d.actual_local_hour BETWEEN @start_hour AND @end_hour\n"
        f" AND ST_DWITHIN(ST_GEOGPOINT(s.longitude, s.latitude), ST_GEOGPOINT(@longitude,@latitude), @radius_m)\n"
        f"GROUP BY d.event_date, d.actual_local_hour, d.stop_id, s.stop_name, d.route_id\n"
        f"ORDER BY straight_line_m, actual_local_hour, route_id LIMIT @limit",
        [event_date, legacy.P_LAT, legacy.P_LON,
         {"name": "start_hour", "type": "integer", "default": 12, "minValue": 0, "maxValue": 23,
          "description": "First included event clock hour, Chicago local time."},
         {"name": "end_hour", "type": "integer", "default": 18, "minValue": 0, "maxValue": 23,
          "description": "Last included event clock hour, Chicago local time."},
         {"name": "radius_m", "type": "integer", "default": 800, "minValue": 100, "maxValue": 5000,
          "description": "Straight-line stop search radius in metres."}, legacy.p_limit(12, 50)]),
        legacy.tool(
        "get_cooling_readiness",
        "Reports operator verification for an explicit event date and canonical facility. Source inventory "
        "classification is separate from confirmed opening, AC, capacity, staffing and accessibility. "
        "Nulls/unconfirmed values must not be represented as operational readiness.",
        f"WITH ready AS (SELECT * FROM `{WORK}.facility_readiness` WHERE event_date=DATE(@event_date)\n"
        f" QUALIFY ROW_NUMBER() OVER(PARTITION BY facility_id ORDER BY readiness_verified_at DESC, retrieved_at DESC)=1)\n"
        f"SELECT f.facility_id,f.facility_name,f.address,f.candidate_status,f.indoor_cooling_classification,\n"
        f" DATE(@event_date) AS event_date, COALESCE(r.readiness_status,'unconfirmed') AS readiness_status,\n"
        f" r.open_for_event,r.working_ac_confirmed,r.available_capacity,r.staffing_confirmed,\n"
        f" r.wheelchair_accessible_confirmed,r.verified_by,r.readiness_verified_at,r.verification_source\n"
        f"FROM `{WORK}.canonical_facilities` f LEFT JOIN ready r USING(facility_id)\n"
        f"WHERE @facility_id='any' OR f.facility_id=@facility_id ORDER BY facility_name,facility_id LIMIT @limit",
        [event_date, {"name": "facility_id", "type": "string", "default": "any",
                      "description": "Canonical facility ID from facility tools, or any for a sample."}, legacy.p_limit(5, 50)]),
        legacy.tool(
        "get_cooling_routes",
        "Precomputed preliminary network routes from a ranked cell to listed indoor inventory candidates for "
        "the explicit planning date. Network paths and straight-line connectors are separate. Walking minutes "
        "assume 60 metres/minute; truck duration and live traffic are unavailable. Facility opening/working AC, "
        "entrance, connectors, wheelchair access and vehicle clearances remain unverified. Zero rows means no prepared route.",
        f"SELECT * FROM `{WORK}.cooling_routes` WHERE event_date=DATE(@event_date) "
        f"AND cell_id=@cell_id AND mode=@mode ORDER BY origin_priority_rank",
        [event_date, {"name": "cell_id", "type": "string",
                      "description": "Cell ID from ranked priority sites; routes are prepared for the top ten cells."},
         {"name": "mode", "type": "string", "default": "walk", "description": "walk or truck; truck duration is unavailable."}]),
    ]
    toolset = docs.pop()
    docs.extend(additions)
    toolset["tools"] += [doc["name"] for doc in additions]
    docs.append(toolset)
    return docs


def render() -> str:
    return "# Generated by build_enriched_tools_yaml.py; opt-in heat_dispatch_enriched toolset.\n" + yaml.dump_all(
        build(), Dumper=legacy._Dumper, sort_keys=False, allow_unicode=True, width=110)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = render()
    if args.check:
        if not OUT.exists() or OUT.read_text() != content:
            raise SystemExit("tools.enriched.yaml is stale; regenerate it")
    else:
        OUT.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
