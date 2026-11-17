# Chicago walking and truck routing network

## Acquired source

BBBike distributes a regularly regenerated OpenStreetMap Chicago regional extract:

- PBF: <https://download.bbbike.org/osm/bbbike/Chicago/Chicago.osm.pbf>
- Source polygon: <https://download.bbbike.org/osm/bbbike/Chicago/Chicago.poly>
- Directory: <https://download.bbbike.org/osm/bbbike/Chicago/>
- OSM attribution/ODbL terms: <https://www.openstreetmap.org/copyright>

The verified download was **103,255,975 bytes** (about 98.5 MiB), last modified
**2026-11-14 17:04:54 UTC**, acquired 2026-11-17. Its SHA-256 is
`569d34f3f845a79d11d0f9d66e0b83716ddbc335e54e7ed119586653cf688c14`.
The downloader streams with a 125 MiB cap. The Illinois Geofabrik extract was
363,652,550 bytes and was excluded from acquisition because of its size.
No API key or repeated bulk calls to the OSM editing API are needed.

The completed local normalization contains **1,377,438 network vertices** and
**1,786,275 segments**: 1,762,977 have pedestrian permission and 825,426 have
truck permission under the documented tag rules. There are **7,230 mapped
entrances**, **170,006 constraint points**, and **4,786 turn relations** (425
include via-way restrictions). No segment was skipped for a missing location.
The normalized JSONL is approximately 900 MB; use backend/local graph queries
instead of loading the full dataset into a browser.

The regional source spans west −88.01, south 41.64, east −87.40, north 42.09.
Normalization is bounded by west −87.95, south 41.60, east −87.50, north 42.10.
Effective rectangular coverage is their intersection: west −87.95,
south 41.64, east −87.50, north 42.09. This covers Chicago's city land, while
the requested outer rectangular halo below 41.64 and above 42.09 is outside
the source. Routes beyond the extract may be incomplete.

Credit **© OpenStreetMap contributors**, and BBBike for providing the extract.
Derived network data retains the ODbL license obligations.

## Reproduce

PyOsmium is the only additional parsing dependency; acquisition uses Python's
standard library. The local environment was prepared with:

```bash
uv pip install --python .venv/bin/python osmium
.venv/bin/python scripts/load_chicago_routing.py
# Replay the acquired source without downloading another regional extract:
.venv/bin/python scripts/load_chicago_routing.py \
  --source-pbf data/enrichment/routing_citywide/Chicago.osm.pbf
```

Default output: git-ignored `data/enrichment/routing_citywide/`.
There are no cloud writes or changes to existing source/model tables.

| Artifact | Contents |
|---|---|
| `Chicago.osm.pbf` | Original regional OSM source |
| `source_download.json` | Retrieval time, source last-modified time, byte count and SHA-256 |
| `routing_nodes.jsonl` | Coordinates for acquired network vertices and available node constraint tags |
| `routing_edges.jsonl` | Way/segment IDs, endpoint IDs, geodesic length, separate walk/truck direction flags, original way tags |
| `routing_entrances.jsonl` | Mapped entrance points and entrance/access/wheelchair tags; availability unverified |
| `routing_constraints.jsonl` | Mapped barriers, crossings, kerbs and access restrictions |
| `routing_turn_restrictions.jsonl` | OSM relation IDs, from/to ways, via nodes/ways, full restriction tags |
| `manifest.json` | Source provenance, bounded coverage, counts, parser diagnostics, assumptions and terms |
| `routing_example_routes.jsonl` | One walking and one truck route to a listed indoor candidate, with unverified connectors and facility status |

`SCHEMAS` exports the explicit schemas of the five normalized network tables,
should upload later be authorized. Source/retrieval provenance is maintained
in the manifest rather than copied onto every segment.

## Walking and truck routes

Pedestrian and truck access are separate flags. Specific `foot` or `hgv` tags
take precedence over general access. Truck routes exclude pedestrian-only ways
and unconfirmed mapped gates/bollards. Vehicle one-way rules do not restrict
walking; explicit `oneway:foot` rules do. Roundabouts are directed for vehicles.

`shortest_route()` computes mapped-network distance with known via-node `no_*`
and `only_*` turn restrictions. Via-way and conditional turn restrictions are
preserved in the source table; the preliminary solver conservatively excludes
their originating ways. This can remove a route that would be allowed under a
specified condition. The original rules remain available for a full routing
engine to interpret later.

`route_between_points()` snaps to mode-appropriate network nodes and reports
origin/destination connector distances separately. Connectors over 150 m have
`connector_requires_review` and null route/time estimates. Shorter connectors
still have `connectors_verified=false` and `entrance_verified=false`.
Walking minutes use an explicit configurable 60 m/min assumption. Truck travel
times are not calculated.

Every successful result is a **preliminary mapped route**. Facility operating
hours, AC status, staffing, gate state, exact truck weight/height/width and
destination-access conditions remain unverified. There is no claim of live or
historical traffic, a safe crossing, wheelchair accessibility, shaded walking,
or an open cooling destination. Route feasibility must be checked against a
vehicle profile and operator-confirmed destination before dispatch.

## Actual route proof

A bounded Loop subgraph (19,543 vertices, 23,470 segments) produced preliminary
routes from 41.8781, −87.6298 to the inventory's **Harold Washington College**:

- Walking: approximately **1,123 m** on the mapped network plus **36 m** of
  unverified connectors; approximately **19.3 minutes** at the explicit 60 m/min
  assumption.
- Truck: approximately **1,270 m** on the mapped network plus **50 m** of
  unverified connectors. No driving time or traffic estimate is supplied.

These examples verify that the acquired topology supports distinct routes. They
do not establish that this college is open, AC is working, or its entrance and
truck approach are usable for the event. The full acquired graph has regional
coverage; this small demonstration subgraph does not establish every citywide
origin/destination pair is connected.
