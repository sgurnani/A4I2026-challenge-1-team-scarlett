#!/usr/bin/env python3
"""Acquire a regional OSM extract and prepare Chicago walk/truck routing graphs.

No API key, cloud writes, live traffic estimates, or confirmed facility access.
The original extract, access/entrance tags, and turn restrictions are preserved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import heapq
import json
import math
import urllib.request
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://download.bbbike.org/osm/bbbike/Chicago/Chicago.osm.pbf"
SOURCE_POLYGON = "https://download.bbbike.org/osm/bbbike/Chicago/Chicago.poly"
BBOX = (-87.95, 41.60, -87.50, 42.10)
SOURCE_BBOX = (-88.01, 41.64, -87.40, 42.09)
DENIED = {"no", "private"}
PERMITTED = {"yes", "designated", "permissive", "destination"}
WALK_EXCLUDED = {"motorway", "motorway_link", "trunk", "trunk_link", "construction", "proposed", "raceway", "bus_guideway"}
TRUCK_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
                  "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
                  "residential", "living_street", "service", "road"}
NODE_KEYS = {"entrance", "access", "foot", "vehicle", "motor_vehicle", "hgv", "wheelchair",
             "barrier", "highway", "crossing", "crossing:signals", "kerb", "level", "door", "indoor"}
SCHEMAS = {
    "routing_nodes": [("node_id", "INT64"), ("latitude", "FLOAT64"), ("longitude", "FLOAT64"), ("tags_json", "STRING")],
    "routing_edges": [("edge_id", "STRING"), ("way_id", "INT64"), ("from_node", "INT64"),
                      ("to_node", "INT64"), ("length_m", "FLOAT64"), ("highway", "STRING"),
                      ("walk_forward", "BOOL"), ("walk_backward", "BOOL"),
                      ("truck_forward", "BOOL"), ("truck_backward", "BOOL"), ("tags_json", "STRING")],
    "routing_entrances": [("node_id", "INT64"), ("latitude", "FLOAT64"), ("longitude", "FLOAT64"), ("tags_json", "STRING")],
    "routing_constraints": [("node_id", "INT64"), ("latitude", "FLOAT64"), ("longitude", "FLOAT64"), ("tags_json", "STRING")],
    "routing_turn_restrictions": [("relation_id", "INT64"), ("from_way", "INT64"), ("to_way", "INT64"),
                                  ("via_node", "INT64"), ("via_way_ids_json", "STRING"),
                                  ("members_json", "STRING"), ("tags_json", "STRING")],
}


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6_371_008.8 * 2 * math.asin(min(1.0, math.sqrt(h)))


def inside(point: tuple[float, float], bbox: tuple[float, ...]) -> bool:
    west, south, east, north = bbox
    return south <= point[0] <= north and west <= point[1] <= east


def access_allowed(tags: dict[str, str], mode: str) -> bool:
    """Specific access tags take precedence over more general ones."""
    highway = tags.get("highway")
    keys: tuple[str, ...]
    if mode == "walk":
        if not highway or highway in WALK_EXCLUDED:
            return False
        keys = ("foot", "access")
    elif mode == "truck":
        if highway not in TRUCK_HIGHWAYS:
            return False
        keys = ("hgv", "motor_vehicle", "vehicle", "access")
    else:
        raise ValueError("mode must be walk or truck")
    for key in keys:
        value = tags.get(key)
        if value in DENIED:
            return False
        if value in PERMITTED:
            return True
    return True


def node_allowed(tags: dict[str, str], mode: str) -> bool:
    keys = ("foot", "access") if mode == "walk" else ("hgv", "motor_vehicle", "vehicle", "access")
    specific = next((tags[key] for key in keys if tags.get(key) in DENIED | PERMITTED), None)
    if specific in DENIED:
        return False
    if mode == "truck" and tags.get("barrier") in {"bollard", "block", "cycle_barrier", "stile", "turnstile", "kissing_gate", "gate", "lift_gate"}:
        return specific in PERMITTED
    return True


def directions(tags: dict[str, str], mode: str) -> tuple[bool, bool]:
    if mode == "walk":
        value = tags.get("oneway:foot", "no")
    else:
        value = tags.get("oneway:motor_vehicle", tags.get("oneway", "yes" if tags.get("junction") == "roundabout" else "no"))
    if value == "-1":
        return False, True
    if value in {"yes", "1", "true"}:
        return True, False
    return True, True


def segment(way_id: int, a: int, b: int, coords_a: tuple[float, float], coords_b: tuple[float, float],
            tags: dict[str, str], nodes: dict[int, dict[str, str]]) -> dict | None:
    walk = access_allowed(tags, "walk") and node_allowed(nodes.get(a, {}), "walk") and node_allowed(nodes.get(b, {}), "walk")
    truck = access_allowed(tags, "truck") and node_allowed(nodes.get(a, {}), "truck") and node_allowed(nodes.get(b, {}), "truck")
    length = distance_m(coords_a, coords_b)
    if not (walk or truck) or a == b or length <= 0:
        return None
    wf, wb = directions(tags, "walk") if walk else (False, False)
    tf, tb = directions(tags, "truck") if truck else (False, False)
    return {"edge_id": f"{way_id}:{a}:{b}", "way_id": way_id, "from_node": a, "to_node": b,
            "length_m": length, "highway": tags["highway"], "walk_forward": wf, "walk_backward": wb,
            "truck_forward": tf, "truck_backward": tb, "tags_json": json.dumps(tags, sort_keys=True)}


def restriction_kind(tags: dict[str, str], mode: str) -> str | None:
    if mode == "walk":
        return tags.get("restriction:foot")
    exceptions = {part.strip() for part in tags.get("except", "").split(";")}
    if exceptions & {"motor_vehicle", "vehicle"}:
        return None
    return tags.get("restriction:hgv", tags.get("restriction:motor_vehicle", tags.get("restriction")))


def turn_allowed(previous_way: int, previous_node: int, node: int, next_way: int, next_node: int,
                 restrictions: list[dict], mode: str) -> bool:
    for restriction in restrictions:
        if restriction["from_way"] != previous_way or restriction["via_node"] != node:
            continue
        kind = restriction_kind(json.loads(restriction["tags_json"]), mode)
        if not kind:
            continue
        target_matches = restriction["to_way"] == next_way
        if kind == "no_u_turn":
            if target_matches and next_node == previous_node:
                return False
        elif kind.startswith("no_") and target_matches:
            return False
        elif kind.startswith("only_") and (not target_matches or next_node == previous_node):
            return False
    return True


def prepare_turn_rules(restrictions: list[dict], mode: str) -> tuple[dict[int, list[dict]], set[int]]:
    indexed: dict[int, list[dict]] = {}
    unsupported_from: set[int] = set()
    for row in restrictions:
        tags = json.loads(row["tags_json"])
        members = json.loads(row.get("members_json", "[]"))
        from_ways = [member["ref"] for member in members if member["role"] == "from" and member["type"] == "w"]
        to_ways = [member["ref"] for member in members if member["role"] == "to" and member["type"] == "w"]
        kind = restriction_kind(tags, mode)
        conditional = any(key.startswith("restriction") and key.endswith(":conditional") for key in tags)
        if kind or (mode == "truck" and conditional):
            if (row["via_node"] is None or len(from_ways) > 1 or len(to_ways) > 1 or conditional
                    or (kind and not kind.startswith(("no_", "only_")))):
                unsupported_from.add(row["from_way"])
                unsupported_from.update(from_ways)
            else:
                indexed.setdefault(row["via_node"], []).append(row)
    return indexed, unsupported_from


def shortest_route(edges: list[dict], origin: int, destination: int, mode: str,
                   restrictions: list[dict] | None = None) -> dict:
    """Dijkstra with known via-node turn restrictions; unknown rules fail closed.

    Vehicle dimensions, gate state, speed/traffic, and final entrances remain
    unverified. Routes are preliminary mapped candidates only.
    """
    if mode not in {"walk", "truck"}:
        raise ValueError("mode must be walk or truck")
    restrictions = restrictions or []
    indexed, unsupported_from = prepare_turn_rules(restrictions, mode)
    graph: dict[int, list[tuple[int, int, float]]] = {}
    for edge in edges:
        if edge["way_id"] in unsupported_from:
            continue  # Do not route through a way with unsupported turn rules.
        a, b = edge["from_node"], edge["to_node"]
        if edge[f"{mode}_forward"]:
            graph.setdefault(a, []).append((b, edge["way_id"], edge["length_m"]))
        if edge[f"{mode}_backward"]:
            graph.setdefault(b, []).append((a, edge["way_id"], edge["length_m"]))
    initial = (origin, -1, -1)
    best = {initial: 0.0}
    previous: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    queue = [(0.0, initial)]
    while queue:
        cost, state = heapq.heappop(queue)
        if cost != best.get(state):
            continue
        node, prior_way, prior_node = state
        if node == destination:
            path = [node]
            while state in previous:
                state = previous[state]
                path.append(state[0])
            return {"network_distance_m": cost, "node_path": list(reversed(path)),
                    "status": "preliminary_mapped_route", "mode": mode,
                    "unsupported_restriction_ways_excluded": len(unsupported_from)}
        for neighbor, way, length in graph.get(node, []):
            if not turn_allowed(prior_way, prior_node, node, way, neighbor, indexed.get(node, []), mode):
                continue
            next_state = (neighbor, way, node)
            candidate = cost + length
            if candidate < best.get(next_state, math.inf):
                best[next_state] = candidate
                previous[next_state] = state
                heapq.heappush(queue, (candidate, next_state))
    return {"network_distance_m": None, "node_path": [], "status": "no_mapped_route", "mode": mode,
            "unsupported_restriction_ways_excluded": len(unsupported_from)}


def route_between_points(nodes: list[dict], edges: list[dict], origin: tuple[float, float],
                         destination: tuple[float, float], mode: str, restrictions: list[dict] | None = None,
                         walking_speed_m_per_min: float = 60, max_connector_m: float = 150) -> dict:
    """Preliminary route with separate, unverified straight-line connectors."""
    if mode not in {"walk", "truck"}:
        raise ValueError("mode must be walk or truck")
    if not math.isfinite(walking_speed_m_per_min) or walking_speed_m_per_min <= 0:
        raise ValueError("walking speed must be positive and finite")
    if not math.isfinite(max_connector_m) or max_connector_m <= 0:
        raise ValueError("connector limit must be positive and finite")
    available: set[int] = set()
    for edge in edges:
        if edge[f"{mode}_forward"] or edge[f"{mode}_backward"]:
            available.update((edge["from_node"], edge["to_node"]))
    candidates = [row for row in nodes if row["node_id"] in available]
    if not candidates:
        raise ValueError("no network nodes for requested mode")

    def nearest(point: tuple[float, float]) -> tuple[int, float]:
        return min(((row["node_id"], distance_m(point, (row["latitude"], row["longitude"])))
                    for row in candidates), key=lambda item: item[1])

    source, origin_snap = nearest(origin)
    target, destination_snap = nearest(destination)
    route = shortest_route(edges, source, target, mode, restrictions)
    if max(origin_snap, destination_snap) > max_connector_m:
        route.update({"status": "connector_requires_review", "network_distance_m": None, "node_path": []})
    route.update({"origin_latitude": origin[0], "origin_longitude": origin[1],
                  "destination_latitude": destination[0], "destination_longitude": destination[1],
                  "origin_snap_m": origin_snap, "destination_snap_m": destination_snap,
                  "connector_distance_m": origin_snap + destination_snap,
                  "connectors_verified": False, "entrance_verified": False,
                  "vehicle_constraints_verified": False, "traffic_data_used": False})
    length = route["network_distance_m"]
    route["estimated_walk_minutes"] = ((length + origin_snap + destination_snap) / walking_speed_m_per_min
                                         if mode == "walk" and length is not None else None)
    route["walking_speed_m_per_min"] = walking_speed_m_per_min if mode == "walk" else None
    return route


def jsonl_rows(path: Path) -> Iterator[dict]:
    with path.open() as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


class PreparedRoutingNetwork:
    """Reusable local graph: stream raw rows once, retain scalar graph data.

    Uses a geographic grid for fast mode-specific snapping. Each candidate query
    uses one bounded Dijkstra search, rather than one search per facility.
    """
    GRID_STEP = 0.002

    def __init__(self, nodes: Iterable[dict], edges: Iterable[dict], restrictions: list[dict] | None = None,
                 modes: tuple[str, ...] = ("walk", "truck"), max_connector_m: float = 150,
                 walking_speed_m_per_min: float = 60, max_expanded_states: int = 200_000) -> None:
        if not modes or set(modes) - {"walk", "truck"}:
            raise ValueError("modes must contain walk and/or truck")
        if not math.isfinite(max_connector_m) or max_connector_m <= 0:
            raise ValueError("connector limit must be positive and finite")
        if not math.isfinite(walking_speed_m_per_min) or walking_speed_m_per_min <= 0:
            raise ValueError("walking speed must be positive and finite")
        if max_expanded_states <= 0:
            raise ValueError("search budget must be positive")
        self.max_connector_m = max_connector_m
        self.walking_speed = walking_speed_m_per_min
        self.max_expanded_states = max_expanded_states
        self.coordinates = {row["node_id"]: (row["latitude"], row["longitude"]) for row in nodes}
        self.graphs: dict[str, dict[int, list[tuple[int, int, float]]]] = {mode: {} for mode in modes}
        self.rules: dict[str, dict[int, list[dict]]] = {}
        self.blocked: dict[str, set[int]] = {}
        for mode in modes:
            self.rules[mode], self.blocked[mode] = prepare_turn_rules(restrictions or [], mode)
        network_nodes: dict[str, set[int]] = {mode: set() for mode in modes}
        edge_count = 0
        for edge in edges:
            a, b, way, length = edge["from_node"], edge["to_node"], edge["way_id"], edge["length_m"]
            if a not in self.coordinates or b not in self.coordinates:
                raise ValueError("edge references a missing network coordinate")
            if not math.isfinite(length) or length <= 0:
                raise ValueError("edge length must be positive and finite")
            edge_count += 1
            for mode in modes:
                if way in self.blocked[mode]:
                    continue
                graph = self.graphs[mode]
                forward, backward = edge[f"{mode}_forward"], edge[f"{mode}_backward"]
                if forward:
                    graph.setdefault(a, []).append((b, way, length))
                if backward:
                    graph.setdefault(b, []).append((a, way, length))
                if forward or backward:
                    network_nodes[mode].update((a, b))
        self.grids: dict[str, dict[tuple[int, int], list[int]]] = {mode: {} for mode in modes}
        for mode, values in network_nodes.items():
            for node in values:
                self.grids[mode].setdefault(self._cell(self.coordinates[node]), []).append(node)
        self.stats = {"coordinate_count": len(self.coordinates), "source_edge_count": edge_count,
                      "mode_node_counts": {mode: len(values) for mode, values in network_nodes.items()},
                      "mode_directed_edge_counts": {mode: sum(map(len, graph.values())) for mode, graph in self.graphs.items()},
                      "unsupported_way_counts": {mode: len(values) for mode, values in self.blocked.items()}}
        self._snap_cache: dict[tuple[float, float, str], tuple[int, float] | None] = {}
        # Original nodes, edges and tags dictionaries are not retained.

    @classmethod
    def from_directory(cls, directory: Path | str, **options: Any) -> PreparedRoutingNetwork:
        directory = Path(directory)
        return cls(jsonl_rows(directory / "routing_nodes.jsonl"), jsonl_rows(directory / "routing_edges.jsonl"),
                   list(jsonl_rows(directory / "routing_turn_restrictions.jsonl")), **options)

    def _cell(self, point: tuple[float, float]) -> tuple[int, int]:
        return math.floor(point[0] / self.GRID_STEP), math.floor(point[1] / self.GRID_STEP)

    def snap(self, point: tuple[float, float], mode: str) -> tuple[int, float] | None:
        """Find the closest mapped mode node within the connector cap."""
        if mode not in self.graphs:
            raise ValueError("requested mode was not prepared")
        lat, lon = point
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 < lat < 90 and -180 <= lon <= 180):
            raise ValueError("invalid route coordinate")
        cache_key = lat, lon, mode
        if cache_key in self._snap_cache:
            return self._snap_cache[cache_key]
        cell = self._cell(point)
        # Expand enough bins to contain the connector circle even at bin edges.
        scale = 111_000 * self.GRID_STEP * max(0.01, math.cos(math.radians(lat)))
        radius = math.ceil(self.max_connector_m / scale) + 1
        nearest: tuple[int, float] | None = None
        for a in range(cell[0] - radius, cell[0] + radius + 1):
            for b in range(cell[1] - radius, cell[1] + radius + 1):
                for node in self.grids[mode].get((a, b), []):
                    distance = distance_m(point, self.coordinates[node])
                    if distance <= self.max_connector_m and (nearest is None or distance < nearest[1]):
                        nearest = node, distance
        self._snap_cache[cache_key] = nearest
        return nearest

    def _search(self, origin: int, targets: dict[int, list[tuple[int, float]]], mode: str,
                limit: int) -> tuple[list[tuple[int, float, list[int]]], str, int]:
        initial = (origin, -1, -1)
        best = {initial: 0.0}
        previous: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        queue = [(0.0, initial)]
        found: dict[int, tuple[float, float, tuple[int, int, int]]] = {}
        visited_targets: set[int] = set()
        expanded = 0
        outcome = "search_complete"
        while queue:
            cost, state = heapq.heappop(queue)
            if cost != best.get(state):
                continue
            if len(found) >= limit:
                threshold = sorted(value[0] for value in found.values())[limit - 1]
                if cost > threshold:
                    break  # All undiscovered targets have nonnegative connector cost.
            if expanded >= self.max_expanded_states:
                outcome = "search_budget_exhausted"
                break
            expanded += 1
            node, prior_way, prior_node = state
            if node in targets and node not in visited_targets:
                for key, connector in targets[node]:
                    found[key] = cost + connector, cost, state
                visited_targets.add(node)
            for neighbor, way, length in self.graphs[mode].get(node, []):
                if not turn_allowed(prior_way, prior_node, node, way, neighbor, self.rules[mode].get(node, []), mode):
                    continue
                next_state = neighbor, way, node
                candidate = cost + length
                if candidate < best.get(next_state, math.inf):
                    best[next_state] = candidate
                    previous[next_state] = state
                    heapq.heappush(queue, (candidate, next_state))
        output = []
        for key, (_score, cost, state) in sorted(found.items(), key=lambda item: item[1][0])[:limit]:
            path = [state[0]]
            while state in previous:
                state = previous[state]
                path.append(state[0])
            output.append((key, cost, list(reversed(path))))
        return output, outcome, expanded

    def _format(self, origin: tuple[float, float], destination: tuple[float, float], mode: str,
                origin_snap: float | None, destination_snap: float | None, length: float | None,
                path: list[int], status: str) -> dict:
        connector = origin_snap + destination_snap if origin_snap is not None and destination_snap is not None else None
        return {"mode": mode, "status": status, "network_distance_m": length, "node_path": path,
                "origin_latitude": origin[0], "origin_longitude": origin[1],
                "destination_latitude": destination[0], "destination_longitude": destination[1],
                "origin_snap_m": origin_snap, "destination_snap_m": destination_snap,
                "connector_distance_m": connector, "connectors_verified": False, "entrance_verified": False,
                "vehicle_constraints_verified": False, "traffic_data_used": False,
                "unsupported_restriction_ways_excluded": len(self.blocked[mode]),
                "estimated_walk_minutes": ((length + connector) / self.walking_speed
                                           if mode == "walk" and length is not None and connector is not None else None),
                "walking_speed_m_per_min": self.walking_speed if mode == "walk" else None,
                "estimated_truck_minutes": None}

    def route(self, origin: tuple[float, float], destination: tuple[float, float], mode: str) -> dict:
        start, end = self.snap(origin, mode), self.snap(destination, mode)
        if start is None or end is None:
            return self._format(origin, destination, mode, start[1] if start else None,
                                end[1] if end else None, None, [], "connector_requires_review")
        hits, outcome, expanded = self._search(start[0], {end[0]: [(0, end[1])]}, mode, 1)
        if hits:
            result = self._format(origin, destination, mode, start[1], end[1], hits[0][1], hits[0][2], "preliminary_mapped_route")
        else:
            result = self._format(origin, destination, mode, start[1], end[1], None, [],
                                  outcome if outcome == "search_budget_exhausted" else "no_mapped_route")
        result.update({"search_outcome": outcome, "expanded_states": expanded})
        return result

    def routes_to_facilities(self, origin: tuple[float, float], facilities: list[dict], mode: str,
                             limit: int = 1) -> list[dict]:
        """Nearest mapped candidates among ALL provided sites, one search.

        Ranking includes unverified connector distance and does not imply a
        facility is open, functioning, accessible, or usable for dispatch.
        """
        if limit <= 0:
            raise ValueError("result limit must be positive")
        start = self.snap(origin, mode)
        if start is None:
            return [{"mode": mode, "status": "connector_requires_review", "network_distance_m": None,
                     "estimated_walk_minutes": None, "estimated_truck_minutes": None,
                     "origin_latitude": origin[0], "origin_longitude": origin[1]}]
        targets: dict[int, list[tuple[int, float]]] = {}
        snaps: dict[int, tuple[int, float]] = {}
        for key, facility in enumerate(facilities):
            snapped = self.snap((facility["latitude"], facility["longitude"]), mode)
            if snapped is not None:
                snaps[key] = snapped
                targets.setdefault(snapped[0], []).append((key, snapped[1]))
        if not snaps:
            return [{"mode": mode, "status": "no_candidate_within_connector_limit", "network_distance_m": None,
                     "estimated_walk_minutes": None, "estimated_truck_minutes": None,
                     "candidate_count": len(facilities), "origin_latitude": origin[0], "origin_longitude": origin[1]}]
        hits, outcome, expanded = self._search(start[0], targets, mode, limit)
        rows = []
        for rank, (key, length, path) in enumerate(hits, 1):
            facility = facilities[key]
            row = self._format(origin, (facility["latitude"], facility["longitude"]), mode,
                               start[1], snaps[key][1], length, path, "preliminary_mapped_route")
            row.update({"destination_site_name": facility.get("site_name"), "destination_index": key,
                        "candidate_rank": rank, "candidate_count": len(facilities),
                        "candidates_within_connector_limit": len(snaps), "search_outcome": outcome,
                        "expanded_states": expanded, "nearest_candidate_rank_verified": outcome == "search_complete",
                        "ranking_basis": "mapped network distance plus unverified connectors among provided listed sites",
                        "facility_open_for_event": None, "facility_ac_verified": None})
            rows.append(row)
        if not rows:
            rows.append({"mode": mode, "status": outcome if outcome == "search_budget_exhausted" else "no_mapped_route",
                         "network_distance_m": None, "estimated_walk_minutes": None, "estimated_truck_minutes": None,
                         "origin_latitude": origin[0], "origin_longitude": origin[1],
                         "candidate_count": len(facilities), "expanded_states": expanded})
        return rows


def download_source(output_dir: Path, limit: int) -> tuple[Path, dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "heat-dispatch-agent/1.0 (Chicago routing research)"})
    digest, total = hashlib.sha256(), 0
    temp = output_dir / "Chicago.osm.pbf.part"
    with urllib.request.urlopen(request, timeout=60) as response:
        length = int(response.headers.get("Content-Length", "0"))
        if length > limit:
            raise ValueError(f"source {length} bytes exceeds download limit {limit}")
        modified = response.headers.get("Last-Modified")
        with temp.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ValueError("stream exceeds download limit")
                digest.update(chunk)
                handle.write(chunk)
    path = output_dir / "Chicago.osm.pbf"
    temp.replace(path)
    manifest = {"source": SOURCE, "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "source_last_modified": modified, "source_bytes": total, "sha256": digest.hexdigest()}
    (output_dir / "source_download.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return path, manifest


def parse_pbf(path: Path, bbox: tuple[float, ...]) -> tuple[dict[str, list[dict]], dict]:
    import osmium

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.coordinates: dict[int, tuple[float, float]] = {}
            self.node_tags: dict[int, dict[str, str]] = {}
            self.edges: list[dict] = []
            self.entrances: list[dict] = []
            self.constraints: list[dict] = []
            self.restrictions: list[dict] = []
            self.invalid_locations = 0

        def node(self, node: Any) -> None:
            if not node.tags:
                return
            if not node.location.valid() or not inside((node.location.lat, node.location.lon), bbox):
                return
            tags = {tag.k: tag.v for tag in node.tags if tag.k in NODE_KEYS}
            if not tags:
                return
            self.node_tags[node.id] = tags
            row = {"node_id": node.id, "latitude": node.location.lat,
                   "longitude": node.location.lon, "tags_json": json.dumps(tags, sort_keys=True)}
            if tags.get("entrance"):
                self.entrances.append(row)
            if any(key in tags for key in {"barrier", "access", "foot", "wheelchair", "crossing", "kerb"}):
                self.constraints.append(row)

        def way(self, way: Any) -> None:
            tags = {tag.k: tag.v for tag in way.tags}
            if not (access_allowed(tags, "walk") or access_allowed(tags, "truck")):
                return
            refs = list(way.nodes)
            for a, b in zip(refs, refs[1:]):
                if not a.location.valid() or not b.location.valid():
                    self.invalid_locations += 1
                    continue
                coords_a, coords_b = (a.location.lat, a.location.lon), (b.location.lat, b.location.lon)
                if not (inside(coords_a, bbox) and inside(coords_b, bbox)):
                    continue
                edge = segment(way.id, a.ref, b.ref, coords_a, coords_b, tags, self.node_tags)
                if edge:
                    self.edges.append(edge)
                    self.coordinates[a.ref], self.coordinates[b.ref] = coords_a, coords_b

        def relation(self, relation: Any) -> None:
            tags = {tag.k: tag.v for tag in relation.tags}
            if tags.get("type") != "restriction":
                return
            members = list(relation.members)
            from_way = next((member.ref for member in members if member.role == "from" and member.type == "w"), None)
            to_way = next((member.ref for member in members if member.role == "to" and member.type == "w"), None)
            via = next((member.ref for member in members if member.role == "via" and member.type == "n"), None)
            via_ways = [member.ref for member in members if member.role == "via" and member.type == "w"]
            if from_way is not None and to_way is not None:
                self.restrictions.append({"relation_id": relation.id, "from_way": from_way, "to_way": to_way,
                                          "via_node": via, "via_way_ids_json": json.dumps(via_ways),
                                          "members_json": json.dumps([{"type": member.type, "ref": member.ref,
                                                                        "role": member.role} for member in members]),
                                          "tags_json": json.dumps(tags, sort_keys=True)})

    handler = Handler()
    locations = osmium.NodeLocationsForWays(osmium.index.create_map("flex_mem"))
    locations.ignore_errors()
    # Record every location in C++, then avoid Python callbacks for untagged
    # vertices and non-network objects. Highway nodes/ways, access markers and
    # all typed relations still reach the handler.
    source_filter = osmium.filter.KeyFilter(*sorted(NODE_KEYS | {"type"}))
    with osmium.io.Reader(str(path)) as reader:
        osmium.apply(reader, locations, source_filter, handler)
    ways = {row["way_id"] for row in handler.edges}
    restrictions = [row for row in handler.restrictions if row["from_way"] in ways or row["to_way"] in ways]
    nodes = [{"node_id": key, "latitude": value[0], "longitude": value[1],
              "tags_json": json.dumps(handler.node_tags.get(key, {}), sort_keys=True)}
             for key, value in sorted(handler.coordinates.items())]
    return {"routing_nodes": nodes, "routing_edges": handler.edges, "routing_entrances": handler.entrances,
            "routing_constraints": handler.constraints, "routing_turn_restrictions": restrictions}, {
                "skipped_segments_missing_locations": handler.invalid_locations,
                "walk_segments": sum(row["walk_forward"] or row["walk_backward"] for row in handler.edges),
                "truck_segments": sum(row["truck_forward"] or row["truck_backward"] for row in handler.edges),
                "turn_restrictions_with_via_ways": sum(bool(json.loads(row["via_way_ids_json"])) for row in restrictions),
            }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/enrichment/routing_citywide")
    parser.add_argument("--source-pbf", type=Path, help="replay a previously downloaded regional extract")
    parser.add_argument("--bbox", type=float, nargs=4, default=BBOX, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--download-limit-mib", type=int, default=125)
    args = parser.parse_args()
    bbox = tuple(args.bbox)
    west, south, east, north = bbox
    if not (-87.95 <= west < east <= -87.50 and 41.60 <= south < north <= 42.10):
        parser.error("bbox must stay within the approved Chicago routing bounds")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.source_pbf:
        source = args.source_pbf
        metadata_path = source.parent / "source_download.json"
        with source.open("rb") as source_handle:
            source_digest = hashlib.file_digest(source_handle, "sha256").hexdigest()
        metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {
            "source": str(source), "retrieved_at": None, "source_last_modified": None,
            "source_bytes": source.stat().st_size, "sha256": source_digest,
        }
        if metadata.get("sha256") != source_digest:
            parser.error("local source SHA-256 does not match acquisition metadata")
    else:
        source, metadata = download_source(args.output_dir, args.download_limit_mib * 1024 * 1024)
    tables, diagnostics = parse_pbf(source, bbox)
    for name, rows in tables.items():
        with (args.output_dir / f"{name}.jsonl").open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row, allow_nan=False) + "\n")
    manifest: dict[str, Any] = {
        **metadata, "normalized_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "requested_bbox": bbox, "source_bbox": SOURCE_BBOX,
        "effective_bbox": (max(west, SOURCE_BBOX[0]), max(south, SOURCE_BBOX[1]),
                           min(east, SOURCE_BBOX[2]), min(north, SOURCE_BBOX[3])),
        "source_polygon_url": SOURCE_POLYGON, "row_counts": {name: len(rows) for name, rows in tables.items()},
        "diagnostics": diagnostics, "parser": "pyosmium", "attribution": "© OpenStreetMap contributors / BBBike extract",
        "license": "ODbL 1.0", "terms_url": "https://www.openstreetmap.org/copyright",
        "coverage": "Chicago regional network clipped to approved bbox; source coverage excludes outer rectangular halo south of41.64/north of42.09",
        "limitations": ["Current mapped network snapshot, not historical or live traffic",
                        "Walk and truck permissions are separate; untagged restrictions can be missing",
                        "Truck mode uses HGV access tags conservatively but no vehicle dimension/weight profile is verified",
                        "Known via-node turns are supported; via-way and conditional turn restrictions are preserved and excluded conservatively by the route solver",
                        "Facility status, staffing, AC, verified entrance, gate state and final connectors remain unconfirmed",
                        "Walking paths can include steps and are not verified wheelchair or shaded routes",
                        "No turn restrictions that reference an unacquired way can establish access beyond the extract"],
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({key: manifest[key] for key in ("source", "source_bytes", "row_counts", "diagnostics")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
