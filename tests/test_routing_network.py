"""Network permissions/directions and real turn topology, without downloads."""
import json

import pytest

from load_chicago_routing import PreparedRoutingNetwork, access_allowed, directions, node_allowed, parse_pbf, route_between_points, segment, shortest_route


def edge(way, start, end, length=1):
    return {"way_id": way, "from_node": start, "to_node": end, "length_m": length,
            "walk_forward": True, "walk_backward": True,
            "truck_forward": True, "truck_backward": True}


def restriction(from_way, to_way, via, **tags):
    return {"from_way": from_way, "to_way": to_way, "via_node": via,
            "via_way_ids_json": "[]", "tags_json": json.dumps(tags)}


def test_specific_pedestrian_permission_overrides_private_general_access():
    tags = {"highway": "service", "access": "private", "foot": "yes"}
    assert access_allowed(tags, "walk") is True
    assert access_allowed(tags, "truck") is False
    assert access_allowed({**tags, "hgv": "yes"}, "truck") is True
    assert access_allowed({"highway": "residential", "hgv": "no"}, "truck") is False


def test_vehicle_oneway_does_not_restrict_walking():
    tags = {"highway": "residential", "oneway": "-1"}
    assert directions(tags, "truck") == (False, True)
    assert directions(tags, "walk") == (True, True)
    assert directions({**tags, "oneway:foot": "yes"}, "walk") == (True, False)
    assert directions({"junction": "roundabout"}, "truck") == (True, False)


def test_steps_and_unconfirmed_gate_do_not_create_truck_edges():
    assert access_allowed({"highway": "steps"}, "walk") is True
    assert access_allowed({"highway": "steps"}, "truck") is False
    assert node_allowed({"barrier": "gate"}, "truck") is False
    assert node_allowed({"barrier": "gate", "motor_vehicle": "yes"}, "truck") is True
    row = segment(1, 10, 11, (41.88, -87.63), (41.881, -87.63), {"highway": "residential"}, {11: {"barrier": "bollard"}})
    assert row["walk_forward"] is True
    assert row["truck_forward"] is False


def test_forbidden_turn_forces_longer_truck_route_but_not_walk():
    edges = [edge(10, 1, 2), edge(20, 2, 3), edge(30, 2, 4, 2), edge(40, 4, 3, 2)]
    rules = [restriction(10, 20, 2, restriction="no_right_turn")]
    truck = shortest_route(edges, 1, 3, "truck", rules)
    walk = shortest_route(edges, 1, 3, "walk", rules)
    assert truck["network_distance_m"] == 5
    assert truck["node_path"] == [1, 2, 4, 3]
    assert walk["network_distance_m"] == 2


def test_only_turn_rule_and_oneway_prevent_unavailable_route():
    edges = [edge(10, 1, 2), edge(20, 2, 3)]
    for row in edges:
        row["truck_backward"] = False
    rules = [restriction(10, 99, 2, restriction="only_right_turn")]
    assert shortest_route(edges, 1, 3, "truck", rules)["network_distance_m"] is None
    assert shortest_route(edges, 3, 1, "truck")["network_distance_m"] is None


def test_unsupported_conditional_turn_is_preserved_and_excluded_conservatively():
    edges = [edge(10, 1, 2), edge(20, 2, 3)]
    rules = [restriction(10, 20, 2, **{"restriction:conditional": "no_right_turn @ (Mo-Fr 08:00-10:00)"})]
    result = shortest_route(edges, 1, 3, "truck", rules)
    assert result["network_distance_m"] is None
    assert result["unsupported_restriction_ways_excluded"] == 1


def test_far_connector_does_not_create_a_claimed_route():
    nodes = [{"node_id": 1, "latitude": 41.88, "longitude": -87.63},
             {"node_id": 2, "latitude": 41.881, "longitude": -87.63}]
    result = route_between_points(nodes, [edge(10, 1, 2)], (41.80, -87.63), (41.881, -87.63), "walk")
    assert result["status"] == "connector_requires_review"
    assert result["network_distance_m"] is None
    assert result["estimated_walk_minutes"] is None
    assert result["connectors_verified"] is False
    assert result["traffic_data_used"] is False


def test_source_parser_preserves_entrances_and_turn_relations(tmp_path):
    pytest.importorskip("osmium")
    source = tmp_path / "fixture.osm"
    source.write_text('''<osm version="0.6">
      <node id="1" lat="41.880" lon="-87.630"/>
      <node id="2" lat="41.881" lon="-87.630"><tag k="entrance" v="yes"/><tag k="barrier" v="gate"/><tag k="hgv" v="yes"/></node>
      <node id="3" lat="41.882" lon="-87.630"/>
      <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/><tag k="oneway" v="yes"/></way>
      <way id="20"><nd ref="2"/><nd ref="3"/><tag k="highway" v="steps"/></way>
      <relation id="100"><member type="way" ref="10" role="from"/><member type="node" ref="2" role="via"/><member type="way" ref="20" role="to"/><tag k="type" v="restriction"/><tag k="restriction" v="no_right_turn"/></relation>
    </osm>''')
    tables, diagnostics = parse_pbf(source, (-87.64, 41.87, -87.62, 41.89))
    assert len(tables["routing_nodes"]) == 3
    assert len(tables["routing_edges"]) == 2
    assert len(tables["routing_entrances"]) == 1
    assert len(tables["routing_constraints"]) == 1
    assert len(tables["routing_turn_restrictions"]) == 1
    assert diagnostics["walk_segments"] == 2
    assert diagnostics["truck_segments"] == 1


def test_prepared_routes_reuse_graph_and_enforce_the_same_turn_rules(tmp_path, monkeypatch):
    import load_chicago_routing

    nodes = [{"node_id": i, "latitude": lat, "longitude": lon}
             for i, lat, lon in [(1, 41.880, -87.630), (2, 41.881, -87.630),
                                 (3, 41.882, -87.630), (4, 41.881, -87.629)]]
    edges = [edge(10, 1, 2), edge(20, 2, 3), edge(30, 2, 4, 2), edge(40, 4, 3, 2)]
    rules = [restriction(10, 20, 2, restriction="no_right_turn")]
    for name, rows in [("routing_nodes", nodes), ("routing_edges", edges), ("routing_turn_restrictions", rules)]:
        (tmp_path / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    network = PreparedRoutingNetwork.from_directory(tmp_path)

    def forbid_reread(_path):
        raise AssertionError("route queries must not reread million-row files")

    monkeypatch.setattr(load_chicago_routing, "jsonl_rows", forbid_reread)
    assert network.route((41.880, -87.630), (41.882, -87.630), "truck")["network_distance_m"] == 5
    assert network.route((41.880, -87.630), (41.882, -87.630), "walk")["network_distance_m"] == 2


def test_nearest_candidate_search_includes_connector_distance():
    nodes = [{"node_id": 1, "latitude": 41.880, "longitude": -87.630},
             {"node_id": 2, "latitude": 41.881, "longitude": -87.630},
             {"node_id": 3, "latitude": 41.8805, "longitude": -87.630}]
    network = PreparedRoutingNetwork(nodes, [edge(10, 1, 2, 111), edge(20, 1, 3, 120)])
    sites = [{"site_name": "First reached, longer connector", "latitude": 41.881, "longitude": -87.6298},
             {"site_name": "Shortest including connector", "latitude": 41.8805, "longitude": -87.630}]
    nearest = network.routes_to_facilities((41.880, -87.630), sites, "walk", limit=1)
    assert nearest[0]["destination_site_name"] == "Shortest including connector"
    assert nearest[0]["network_distance_m"] == 120
    assert nearest[0]["nearest_candidate_rank_verified"] is True
    assert nearest[0]["facility_open_for_event"] is None
    assert nearest[0]["facility_ac_verified"] is None


def test_prepared_far_connectors_and_truck_duration_stay_unverified():
    nodes = [{"node_id": 1, "latitude": 41.880, "longitude": -87.630},
             {"node_id": 2, "latitude": 41.881, "longitude": -87.630}]
    network = PreparedRoutingNetwork(nodes, [edge(10, 1, 2, 111)])
    near = network.route((41.880, -87.630), (41.881, -87.630), "truck")
    far = network.route((41.80, -87.63), (41.881, -87.63), "truck")
    assert near["network_distance_m"] == 111
    assert near["estimated_truck_minutes"] is None
    assert near["connectors_verified"] is False
    assert far["status"] == "connector_requires_review"
    assert far["network_distance_m"] is None


def test_search_budget_exhaustion_does_not_claim_no_route():
    nodes = [{"node_id": 1, "latitude": 41.880, "longitude": -87.630},
             {"node_id": 2, "latitude": 41.881, "longitude": -87.630}]
    network = PreparedRoutingNetwork(nodes, [edge(10, 1, 2, 111)], max_expanded_states=1)
    result = network.route((41.880, -87.630), (41.881, -87.630), "walk")
    assert result["status"] == "search_budget_exhausted"
    assert result["network_distance_m"] is None
