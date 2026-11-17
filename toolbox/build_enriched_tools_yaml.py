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
                "  nearest_cooling_event_verified, original_tract_geo_id, tract_changed,\n"
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
