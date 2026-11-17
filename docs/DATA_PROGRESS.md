# Enrichment implementation status

User authorized priorities 1–6 and commit/push on November 17, 2026.
Dependent BigQuery outputs are built in priority order. Independent acquisition
and reconciliation preparation can run in parallel.

Source baseline: `qwiklabs-gcp-04-3e58e01275b8.a4i_climate_backup_20261117_214035`.
Output dataset: `qwiklabs-gcp-04-3e58e01275b8.a4i_climate_work_v2`.
Original `a4i_climate` tables and models remain available; no consumer switch
has been made. Recovery instructions are in [DATA_RECOVERY.md](DATA_RECOVERY.md).

## Priority 1 — validated in BigQuery

Outputs: `tract_boundaries_2024`, `chicago_boundary_2024`, `cell_tract_map`,
`tract_acs_2024`, `tract_vulnerability_2024`.

- All **5,053 unique cells** retained; every cell has exactly one containing
  Cook County tract.
- **1,305 assignments corrected**, covering **784 distinct mapped tracts**.
- Heat labels, observation counts and spatial hold-out membership unchanged:
  1,649 measured cells, 442 held-out measured cells.
- Three cell centers disagree with the Census city boundary; one requires
  tract `17031820901`, excluded by the primary Chicago-overlap selection.
  It is explicitly retained as an additional boundary tract.
- ACS2024 county source has **1,332 unique tracts**. The working vulnerability
  table has **800**: 799 primary Chicago-intersecting tracts plus that one
  required county-edge tract. Estimates remain whole-tract values.
- Population, 65+, no-vehicle households, poverty counts/universes, social
  indicators, median age and income use the same 2020–2024 estimate period,
  with count/median margins of error and explicit universes retained.
- Three zero-denominator tracts have null rates and no vulnerability rank;
  no median imputation was needed for populated tracts.

Live assertions check key uniqueness, complete cell/ACS coverage, vintage,
universe consistency and unchanged labels/splits. Fourteen focused tests and
the acquisition/runner typechecks passed. Job IDs and aggregate results are
recorded in ignored `data/enrichment/priorities/progress.json`.

## Priority 2 — inventory validated; operator readiness pending

Outputs: `canonical_facilities`, `facility_source_crosswalk`, `facility_crosswalk`,
`facility_readiness_seed`, `facility_readiness`, plus compatibility views.

- 995 canonical facilities and 2,463 source rows; all 187 legacy schools remain
  individually linked. There are 170 high-school candidates in the imported
  CPS snapshot, and 35 legacy schools lack a matching newer CPS record.
- 106 legacy school coordinates disagree by more than 250 metres with matched
  newer CPS coordinates. The newer source coordinates are preferred; every
  original location and conflict remains in the crosswalk.
- The conservative subset contains 134 listed indoor inventory sites. All 43
  Park District facilities and school candidates retain unconfirmed cooling
  eligibility. No working AC, public opening, capacity or staffing is inferred.
- All 995 readiness seeds are unconfirmed. Inventory refreshes insert only
  missing seed records; they never replace operator confirmations.
- Source metadata identifies legacy schools as 2013–2014, imported CPS locations
  as 2025–2026 and cooling inventory as Summer 2024. Snapshot membership is not
  evidence of current operation. See [facility_crosswalk.md](sources/facility_crosswalk.md).

## Priority 3 — context and opt-in toolset validated

`site_context_enriched` has 5,053 unique cells, unchanged labels/splits and no
missing Census joins. It retains two missing surface offsets, 39 cells with
fewer than five Landsat scenes, 38 cells without a housing-burden rate and 37
cells without a vulnerability rank as explicit gaps. Geographic dispatch
candidates exclude three Census city-boundary disagreements and unknown ranks;
the full context and evaluation cohort remain intact.

The generated `toolbox/tools.enriched.yaml` supplies the opt-in
`heat_dispatch_enriched` toolset. All 11 original tool SQL queries passed live
BigQuery execution, including 494 distinct inventory/candidate listings and
784 tract map records. Population summaries use household and poverty universes
correctly. The frozen original air-offset model continues to receive its
unchanged physical inputs; the new physical layers are reported as context.

Deterministic dispatch assembly preserves readiness flags and no longer invents
school AC-gym hours or large-capacity shelter claims. Thirty-three focused
dispatch/tool tests and changed-file typechecks passed. Production agent/Toolbox
configuration has not been switched.

To prepare a separate local Toolbox server and connect the existing agent:

```bash
.venv/bin/python toolbox/build_enriched_tools_yaml.py
.venv/bin/python scripts/render_toolbox.py \
  --src toolbox/tools.enriched.yaml --out toolbox/tools.enriched.rendered.yaml
# Serve that rendered file with MCP Toolbox, then set TOOLBOX_URL to its endpoint
# and TOOLBOX_TOOLSET=heat_dispatch_enriched for the agent process.
```

## Priority 4 — event-date data validated

Prepared and published November 17, 2026 as a data-validation date, with
append-only run histories and latest committed-run views. The outputs include
`event_transit_departures`, `event_transit_validity`, `event_weather_hourly`,
`event_weather_summary`, `event_data_validity` and their history tables.

- 263,715 stop/route/hour aggregates represent 1,224,027 scheduled stop
  departures. This is not a count of unique trips or passengers.
- 5,218 aggregates belong to the prior GTFS service date but occur on the event
  clock date. Conversion preserves UTC, Chicago local time and DST fold.
- 102 forecast periods across six grids cover 11/24 civil-day hours and 9/16
  daytime hours. Full-day/daytime highs remain null.
- All six 22:00–06:00 overnight windows are complete; forecast lows are
  40–43°F. The source reference time is 22:02:47 UTC, with snapshots about
  2.54 hours old then. Queries report current freshness separately.
- Historical runs remain recoverable. A latest validity record commits each
  run; an empty refresh cannot expose stale rows from an earlier run.
- The opt-in toolset adds event-weather, nearby scheduled transit and
  operator-readiness queries. All **14 tool SQL queries** passed live BigQuery.

Ten event-specific tests, seven write-guard tests and changed-file typechecks
passed. See [event_data.md](sources/event_data.md).

## Priority 5 — exploratory model comparison completed

Two new models are in the working dataset: `heat_offset_candidate` and
`heat_offset_candidate_lst`. `model_predictions` retains all 5,053 cells for
each of seven comparisons, and `model_validation` contains seven metric rows.
Candidates use 1,207 training cells and the same 442 held-out measured cells;
demographics and population density are excluded from candidate inputs.

| Model | Held-out RMSE °F | R² |
|---|---:|---:|
| Frozen original | 2.060 | 0.259 |
| Frozen v2 | 1.870 | 0.389 |
| Frozen v3 | 1.534 | 0.589 |
| Frozen physical boosted tree | 1.592 | 0.557 |
| New physical + NLCD candidate | 2.075 | 0.248 |
| New physical + NLCD + quality-gated LST | 2.046 | 0.269 |
| Predict zero offset | 2.406 | −0.011 |

The new LST candidate's gain over the original is small. No model was promoted.
The previously trained v2/v3 used 1,283 training cells, including low-observation
cells excluded from candidate training, so they are historical comparisons with
a cohort difference. All scores are exploratory because prior experiments reused
this hold-out. Heat-only rank correlations/top-20 overlaps are recorded; these
are distinct from vulnerability-weighted dispatch rankings.

The campaign observation date remains unresolved. Source metadata creation and
export timestamps are not substituted for it. Surface offsets remain predictors
of an air-offset target, never values added directly to a forecast.
Eight model tests, read-only preflight, complete prediction/label checks and
typechecks passed. See [enriched_model.md](sources/enriched_model.md).

## Priority 6 — network and preliminary routes validated

Outputs: `routing_nodes`, `routing_edges`, `routing_entrances`,
`routing_constraints`, `routing_turn_restrictions`, `routing_source`,
`cooling_routes`, `truck_travel_times` and `routing_access_review`.

- Regional OSM/BBBike extract: 1,377,438 nodes and 1,786,275 segments, including
  1,762,977 walking-permitted and 825,426 truck-permitted segments.
- 7,230 entrances and 4,786 turn relations retained, with complete relation
  members. Conditional/via-way restrictions are preserved; unsupported truck
  rules conservatively exclude 810 originating ways from routing.
- The local reusable graph builds in about eight seconds with peak memory
  about 1,384 MiB. Candidate queries search all 134 listed indoor sites.
- Twenty results cover ten priority cells in both modes. All ten walking routes
  and nine truck routes have mapped paths. Walking network distances range
  833–3,170 metres; estimated times are 14.2–53.8 minutes at 60 metres/minute,
  including separately recorded, unverified endpoint connectors.
- Cell `13954_-21918` has an unavailable truck route under the chosen snap:
  the nearest node is on a two-node service-road component whose exits have
  mapped gates without vehicle-access permission tags. An alternative staging
  node 64.41 metres from the center has a 1,615-metre mapped route to listed
  Central West. Its connection to the center needs review. The failed route
  is retained; `routing_access_review` records the source-node/way evidence.
- Truck durations remain null. No traffic, vehicle-clearance, entrance,
  wheelchair, opening, working-AC or connector verification is inferred.

Twelve routing tests, two provenance tests, live node/edge/route assertions and
typechecks passed. The opt-in toolset now has **15 live-verified SQL tools**,
including route retrieval. See [routing_network.md](sources/routing_network.md).

## Final audit and remaining operational inputs

The working dataset contains **36 tables/views and two candidate models**,
with **3,663,979 rows in native tables**. Views do not add to that total.
Final checks confirm 5,053 context cells, zero changed heat labels/splits,
all 187 legacy school source rows, 35,371 model predictions and 20 route results.
All **38 baseline snapshots** and **four backup model copies** remain present.
All 11 changed/source Python files pass mypy. Evidence is saved in ignored
`data/enrichment/priorities/final_verification.json` and per-job progress files.

The engineering outputs for priorities 1–6 are published and validated. Actual
operator readiness is still pending; truck travel times need traffic/provider,
vehicle and depot inputs, and the gated origin needs staging-point review.
November 17 is the validation date, not an asserted heat emergency. Production
agent/Toolbox settings have not been switched by this enrichment work.

Source table archives remain local as well as in the recovery dataset. Raw
acquisition snapshots are cached locally; derived working tables are in
BigQuery and reproduced by the scripts. Git contains code and documentation,
not raw source archives.

## Reproduce the workflow

Prerequisites: the original recovery baseline in BigQuery and acquisition caches
described in [DATA_ENRICHMENT.md](DATA_ENRICHMENT.md), including tract geometry,
facility source snapshots, CTA GTFS and NWS forecasts. Fetch geometry/social
sources first when starting from a fresh checkout.

```bash
.venv/bin/python scripts/load_chicago_acs_aligned.py
.venv/bin/python scripts/run_data_priorities.py geography
.venv/bin/python scripts/run_data_priorities.py census
.venv/bin/python scripts/reconcile_facilities.py --fetch \
  --project qwiklabs-gcp-04-3e58e01275b8
.venv/bin/python scripts/run_data_priorities.py facilities
.venv/bin/python scripts/run_data_priorities.py context
.venv/bin/python scripts/check_enriched_tools.py
.venv/bin/python scripts/prepare_event_data.py --event-date 2026-11-17
.venv/bin/python scripts/run_data_priorities.py event --event-date 2026-11-17
.venv/bin/python scripts/evaluate_enriched_model.py --include-lst --execute
# For an existing pair of candidate models, add --score-only to reevaluate.
.venv/bin/python scripts/load_chicago_routing.py
.venv/bin/python scripts/run_data_priorities.py routing
.venv/bin/python scripts/build_cooling_routes.py --event-date 2026-11-17
.venv/bin/python scripts/check_enriched_tools.py
```

The runner writes only to a separate working dataset and records completed
jobs. Its SQL guard rejects writes to original or backup namespaces. Local
source caches and recovery archives are excluded from Git.
