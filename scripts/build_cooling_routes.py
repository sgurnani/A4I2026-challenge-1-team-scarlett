#!/usr/bin/env python3
"""Publish preliminary network routes for ranked cells, preserving uncertainty."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from backup_bigquery import save_json
from load_chicago_routing import PreparedRoutingNetwork
from render_toolbox import resolve
from run_data_priorities import ROOT, WorkRunner

sys.path.insert(0, str(ROOT / "toolbox"))
import build_enriched_tools_yaml

SCHEMA = [
    ("route_id", "STRING"), ("event_date", "DATE"), ("cell_id", "STRING"),
    ("origin_priority_rank", "INT64"), ("origin_priority_score", "FLOAT64"),
    ("facility_id", "STRING"), ("facility_name", "STRING"), ("mode", "STRING"),
    ("route_status", "STRING"), ("network_distance_m", "FLOAT64"),
    ("connector_distance_m", "FLOAT64"), ("origin_connector_m", "FLOAT64"),
    ("destination_connector_m", "FLOAT64"), ("estimated_walk_minutes", "FLOAT64"),
    ("walking_speed_m_per_min", "FLOAT64"), ("estimated_truck_minutes", "FLOAT64"),
    ("origin_latitude", "FLOAT64"), ("origin_longitude", "FLOAT64"),
    ("destination_latitude", "FLOAT64"), ("destination_longitude", "FLOAT64"),
    ("network_geometry_geojson", "STRING"), ("connectors_geometry_geojson", "STRING"),
    ("search_outcome", "STRING"), ("expanded_states", "INT64"),
    ("candidate_count", "INT64"), ("candidates_within_connector_limit", "INT64"),
    ("source_sha256", "STRING"), ("source_last_modified", "STRING"),
    ("connectors_verified", "BOOL"), ("entrance_verified", "BOOL"),
    ("vehicle_constraints_verified", "BOOL"), ("traffic_data_used", "BOOL"),
    ("facility_open_for_event", "BOOL"), ("facility_ac_verified", "BOOL"),
    ("route_operationally_verified", "BOOL"), ("prepared_at", "TIMESTAMP"),
]


def normalize_route(origin: dict, raw: dict, facilities: list[dict], coordinates: dict,
                    event_date: str, manifest: dict, prepared_at: str) -> dict:
    index = raw.get("destination_index")
    facility = facilities[index] if index is not None else {}
    mode = raw["mode"]
    points = [[coordinates[node][1], coordinates[node][0]] for node in raw.get("node_path", [])]
    geometry = ({"type": "LineString", "coordinates": points} if len(points) > 1
                else {"type": "Point", "coordinates": points[0]} if points else None)
    connectors = None
    if points:
        connectors = {"type": "MultiLineString", "coordinates": [
            [[origin["longitude"], origin["latitude"]], points[0]],
            [points[-1], [facility["longitude"], facility["latitude"]]],
        ]}
    return {
        "route_id": f"{event_date}:{origin['cell_id']}:{mode}", "event_date": event_date,
        "cell_id": origin["cell_id"], "origin_priority_rank": origin["rank"],
        "origin_priority_score": origin["priority_score"], "facility_id": facility.get("facility_id"),
        "facility_name": facility.get("site_name"), "mode": mode, "route_status": raw["status"],
        "network_distance_m": raw.get("network_distance_m"), "connector_distance_m": raw.get("connector_distance_m"),
        "origin_connector_m": raw.get("origin_snap_m"), "destination_connector_m": raw.get("destination_snap_m"),
        "estimated_walk_minutes": raw.get("estimated_walk_minutes"),
        "walking_speed_m_per_min": raw.get("walking_speed_m_per_min"), "estimated_truck_minutes": None,
        "origin_latitude": origin["latitude"], "origin_longitude": origin["longitude"],
        "destination_latitude": facility.get("latitude"), "destination_longitude": facility.get("longitude"),
        "network_geometry_geojson": json.dumps(geometry) if geometry else None,
        "connectors_geometry_geojson": json.dumps(connectors) if connectors else None,
        "search_outcome": raw.get("search_outcome", raw["status"]), "expanded_states": raw.get("expanded_states"),
        "candidate_count": raw.get("candidate_count", len(facilities)),
        "candidates_within_connector_limit": raw.get("candidates_within_connector_limit"),
        "source_sha256": manifest["sha256"], "source_last_modified": manifest["source_last_modified"],
        "connectors_verified": False, "entrance_verified": False, "vehicle_constraints_verified": False,
        "traffic_data_used": False, "facility_open_for_event": None, "facility_ac_verified": None,
        "route_operationally_verified": False, "prepared_at": prepared_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="qwiklabs-gcp-04-3e58e01275b8")
    parser.add_argument("--source-dataset", default="a4i_climate_backup_20261117_214035")
    parser.add_argument("--work-dataset", default="a4i_climate_work_v2")
    parser.add_argument("--event-date", default="2026-11-17")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    dt.date.fromisoformat(args.event_date)
    if not 1 <= args.limit <= 25:
        parser.error("--limit must be between 1 and 25")
    runner = WorkRunner(args.project, args.source_dataset, args.work_dataset)
    if "5" not in runner.report["priorities"] or "routing_network" not in runner.report:
        raise ValueError("Publish and validate models and the routing network first")
    rank_tool = next(doc for doc in build_enriched_tools_yaml.build() if doc.get("name") == "rank_priority_sites")
    sql = resolve(rank_tool["statement"], {"GOOGLE_CLOUD_PROJECT": args.project,
                   "BQ_ENRICHED_DATASET": args.work_dataset, "BQ_BASELINE_DATASET": args.source_dataset})
    parameters = {parameter["name"]: parameter["default"] for parameter in rank_tool["parameters"]}
    parameters["limit"] = args.limit
    sql = re.sub(r"@(\w+)", lambda match: str(parameters[match[1]]), sql)
    origins = runner.query("preliminary_route_origins", sql)
    facilities = runner.query("listed_indoor_route_candidates", """SELECT facility_id, facility_name AS site_name,
      latitude, longitude FROM `{{PROJECT}}.{{WORK}}.canonical_facilities`
      WHERE is_indoor_inventory_access_candidate AND NOT identity_review_required
        AND latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY facility_id""")
    directory = ROOT / "data/enrichment/routing_citywide"
    manifest = json.loads((directory / "manifest.json").read_text())
    print(f"Preparing graph for {len(origins)} origins and {len(facilities)} listed candidates", flush=True)
    network = PreparedRoutingNetwork.from_directory(directory)
    prepared_at = dt.datetime.now(dt.timezone.utc).isoformat()
    routes = []
    for origin in origins:
        point = origin["latitude"], origin["longitude"]
        for mode in ("walk", "truck"):
            raw = network.routes_to_facilities(point, facilities, mode, limit=1)[0]
            row = normalize_route(origin, raw, facilities, network.coordinates, args.event_date, manifest, prepared_at)
            routes.append(row)
            print(f"Route {len(routes)}: {origin['cell_id']} {mode}: {row['route_status']}", flush=True)
    save_json(directory / "priority_routes.json", routes)
    runner.load("cooling_routes", routes, SCHEMA)
    evidence = runner.sql("06_cooling_routes.sql")
    runner.complete("6", {"network": runner.report["routing_network"], "routes": evidence,
                          "operator_readiness_pending": True, "traffic_aware_truck_times_available": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
