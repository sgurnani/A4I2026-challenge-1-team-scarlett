"""Route normalization preserves unavailable operational facts and geometries."""
import json

from build_cooling_routes import SCHEMA, normalize_route


def test_route_records_keep_connectors_separate_and_no_truck_duration():
    row = normalize_route({"cell_id": "c", "rank": 1, "priority_score": 0.9, "latitude": 41.8, "longitude": -87.6},
        {"mode": "truck", "status": "preliminary_mapped_route", "destination_index": 0,
         "node_path": [1, 2], "network_distance_m": 500, "origin_snap_m": 10, "destination_snap_m": 15,
         "connector_distance_m": 25, "estimated_truck_minutes": 2},
        [{"facility_id": "f", "site_name": "Library", "latitude": 41.81, "longitude": -87.61}],
        {1: (41.8, -87.6), 2: (41.81, -87.61)}, "2026-11-17",
        {"sha256": "source", "source_last_modified": "date"}, "2026-11-17T22:00:00Z")
    assert set(row) == {field for field, _ in SCHEMA}
    assert row["estimated_truck_minutes"] is None
    assert row["facility_open_for_event"] is None
    assert row["route_operationally_verified"] is False
    assert json.loads(row["network_geometry_geojson"])["type"] == "LineString"
    assert json.loads(row["connectors_geometry_geojson"])["type"] == "MultiLineString"


def test_unavailable_route_remains_an_explicit_result():
    row = normalize_route({"cell_id": "c", "rank": 1, "priority_score": 0.9, "latitude": 41.8, "longitude": -87.6},
        {"mode": "walk", "status": "connector_requires_review"}, [], {}, "2026-11-17",
        {"sha256": "source", "source_last_modified": "date"}, "2026-11-17T22:00:00Z")
    assert row["network_distance_m"] is None
    assert row["facility_id"] is None
    assert row["network_geometry_geojson"] is None
    assert row["route_status"] == "connector_requires_review"
