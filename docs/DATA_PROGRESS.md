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

## Priority 6

In progress. Regional routing acquisition was prepared independently.
Operator readiness remains unconfirmed; routes to listed indoor facilities
will be labeled preliminary. Traffic-aware truck times require an appropriate
provider and vehicle/depot assumptions.

## Reproduce priority 1

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
```

The runner writes only to a separate working dataset and records completed
jobs. Its SQL guard rejects writes to original or backup namespaces. Local
source caches and recovery archives are excluded from Git.
