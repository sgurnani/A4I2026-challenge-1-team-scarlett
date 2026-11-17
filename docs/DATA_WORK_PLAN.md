# Remaining data work: proposal after the live BigQuery audit

Dataset: `qwiklabs-gcp-04-3e58e01275b8.a4i_climate`.

The live audit on November 17, 2026 inspected **36 tables and four models**,
then ran ten aggregate checks on actual table contents. Evidence is saved in
`data/enrichment/live_inventory.json` and `data/enrichment/live_data_audit.json`.
This document proposes work; it does not change BigQuery tables or models.

## Recovery and proposed table layout

A recovery baseline was created separately from this proposal. At 13:40:35 PST
on November 17, the dataset contained **38 tables and four models**, including
two empty dispatch state tables added after the original audit. All 38 tables
were snapshotted into `a4i_climate_backup_20261117_214035`; the four models were
copied there, and all table data were archived locally. Three local archive
restores and a snapshot restore of all 187 high-school records passed.
See [DATA_RECOVERY.md](DATA_RECOVERY.md) for evidence and restoration steps.

**Recommendation:** create a separate `a4i_climate_work_v2` dataset for the work
below. That dataset and priorities 1–3 outputs are now created and validated;
see [DATA_PROGRESS.md](DATA_PROGRESS.md). Preserve
the current `a4i_climate` tables and model names until outputs pass validation
and an explicit consumer switch is agreed. Keep source inventories separate
from reconciled outputs and operator-entered readiness records.

| Priority | Proposed outputs in `a4i_climate_work_v2` |
|---|---|
| 1 | `cell_tract_map`, `tract_vulnerability_2024` |
| 2 | `facility_crosswalk`, `facility_readiness` |
| 3 | `site_context_enriched` |
| 4 | `weather_event`, `transit_departures_event`, with event-date keys |
| 5 | `heat_offset_candidate`, `model_validation` |
| 6 | `cooling_routes`, `truck_travel_times` |

The user approved priorities 1–6 on November 17, 2026; implementation results
are tracked in [DATA_PROGRESS.md](DATA_PROGRESS.md). Recommended order is
1 and 2 first, then 3 and 4; model comparison and routing follow the validated
context and verified destinations. Public-source acquisition can proceed after
review; operator readiness needs confirmation from the responsible operators.
Any contact with operators or other teams requires separate authorization.

## What is already available

| Data | Live contents | Remaining issue |
|---|---|---|
| Heat observations | 48,855 readings; temperature, humidity and heat index all populated | Every `observed_at` is null; campaign date/alignment needs source metadata |
| City grid and context | 5,053 unique cells in both `grid_cells` and `site_context`; 1,649 measured cells, including 1,207 training and 442 held out | Current nearest-centroid tract assignment needs geographic correction |
| Per-cell land cover | `cell_land_cover`: 5,053 rows; canopy and imperviousness have no missing values | Integrate physical features with documented 2021 vintage and percent units |
| Satellite surface temperature | `cell_surface_temp`: 5,053 rows, 5,051 usable offsets; 70,742 cell/scene records from 14 scenes, 2019–2025 | Two missing offsets and 39 cells with fewer than five clear scenes; surface temperature is a different quantity from air temperature |
| Census vulnerability and housing | 1,332 Cook County social rows; Chicago subsets, housing and tract canopy each have 799 rows; ACS 2024 | Current context uses 793 tract keys, of which 20 do not match the Chicago subset; four tract-canopy and three housing rates are null |
| Existing facilities | `cooling_facilities`: 474 rows; `high_schools`: 187 rows | School records also appear in the existing cooling inventory; source overlap must be reconciled by identifiers |
| Additional facility sources | `chi_site_candidate_sites`: 457 unique keys, including 170 candidate high schools; CPS source: 645 schools; library source: 82 branches | Candidate eligibility and source filters differ; raw listings do not establish event readiness |
| Cooling snapshot | 287 sites, including 134 screened indoor sites | No populated event-open status, capacity, confirmed AC or confirmed accessibility |
| Weather | 936 hourly forecasts over six grids, valid Nov. 17–24; 36 complete overnight summaries | Select the actual event date/time and forecast reference; refresh as needed |
| Transit | 11,107 stop/station records, 132 routes, 263,715 departure aggregates | Departure aggregates cover Nov. 17 only; calendars cover Oct. 29–Dec. 31 |
| Walking | 4,527 nodes, 5,528 segments, one sample library access record | Small Loop pilot; citywide travel times are not available |
| Models | `heat_offset_model`, `heat_offset_v2`, `heat_offset_v3`, `exp_bt_phys` | None of their recorded input schemas includes the new per-cell land-cover or Landsat fields |

## Priority 1: correct geography and align Census inputs

The polygon check covers **5,052 of 5,053 cells**, with no ambiguous polygon
matches. For **1,304 cells**, the tract containing the cell differs from the
current nearest-centroid assignment. This is more consequential than the
20 unmatched old tract keys alone.

Build a versioned `cell_tract_map` using actual tract polygons. Investigate the
one unmatched cell and record its status explicitly. Preserve the original cell
IDs, heat labels and spatial hold-out membership. Use left joins throughout.

Align population, age, no-vehicle and poverty inputs to a consistent ACS vintage.
The added social/housing data use ACS 2024; existing demographics use an older
source, and population estimates differ in 784 of 787 matching records checked
earlier. Fetch the targeted ACS 2024 fields needed to complete a consistent
vulnerability dataset. Retain source years, universes and margins of error.

**Acceptance:** 5,053 unique output cells; at most one assigned tract per cell;
every geographic gap explicit; no population totals inflated by cell joins.

## Priority 2: reconcile facility sources and collect readiness

Create a facility crosswalk with stable keys linking cooling sites, schools,
libraries, senior centers and police stations. Reconcile the 187 schools in the
dedicated table against the 170 candidate-school entries by source identifiers
and eligibility rules. Do not treat different table counts as a loss of records.

Resolve the park classification discrepancy: the 43 Park District facilities are
marked indoor/air-conditioned in `chi_site_candidate_sites`, while the cooling
snapshot screening excludes that category from its indoor subset. Record the
source and confidence of each classification.

Collect operator-verified opening, working AC, available capacity, staffing and
accessibility for the planned heat event. Store confirmations separately from
downloaded inventories so a source refresh cannot erase operator updates.

**Acceptance:** distinct facilities can be identified across sources; candidates
and designated sites are labeled; event readiness includes verification time;
unknown capacity or AC is never treated as a confirmed operational fact.

## Priority 3: publish an enriched cell context and connect the tools

Build `site_context_enriched` with **one row per cell**, combining the corrected
tract mapping, consistent vulnerability inputs, per-cell land cover, Landsat
signal and data-quality flags. Select latest snapshots before joining appended
Census/canopy/housing records. Nulls retain their meaning.

Use the public-domain per-cell NLCD/USFS canopy and imperviousness as the primary
physical layers. Keep the higher-resolution CMAP/Ecopia tract canopy as a
separate comparison source with its unresolved reuse terms documented. Percent
values, fractions, air offsets and surface offsets must have distinct names and
units.

Keep demographic signals in the vulnerability/dispatch layer. Update Toolbox
queries and the agent's deterministic output assembly to consume the agreed
enriched context. Define action-specific use of older adults living alone,
disability and no-car households before adjusting ranking weights.

**Acceptance:** all existing cells represented once; no duplicate snapshot keys;
source/vintage visible; dispatch outputs use stored query results; equity and
coverage checks accompany ranking changes.

## Priority 4: make weather and transit event-specific

Regenerate scheduled departures for the actual planning date from the existing
GTFS calendars and exceptions. Refresh the feed when its validity requires it.
Select forecast hours and complete overnight windows for the same event, using
an explicit geographic/reference forecast convention.

Keep time-indexed weather and transit in separate tables and join only the
requested date/time. Joining all forecast hours or all transit departures into
the cell context would multiply records and distort counts.

**Acceptance:** every recommendation has a valid event date/time and freshness
status; unavailable conditions remain explicit; forecasts are not substituted
for historical campaign observations.

## Priority 5: validate a model candidate using the physical enrichments

Evaluate a versioned candidate using the new NLCD/USFS features and, if justified,
the Landsat signal. Compare it with the existing models under the documented
spatial split and matching labels. Record performance and ranking stability;
account for prior experimentation on the same hold-out when interpreting scores.

Recover the heat campaign date from source metadata if possible, so the 2021
land cover and 2019–2025 satellite scenes can be assessed for temporal alignment.
Use scene count, IQR and missingness when interpreting satellite values. The
prediction remains an **air-temperature offset**; a surface offset is not added
directly to a weather forecast. Observational features do not establish the
causal effect of planting trees or changing pavement.

**Acceptance:** documented comparison and limitations; no exaggerated accuracy
claim; missing satellite values handled without dropping cells; explanations
distinguish the physical quantities used.

## Priority 6: acquire routing for the operational destinations

Obtain a citywide pedestrian network from a suitable regional OSM extract or a
licensed routing service. Start with routes from the highest-priority locations
to verified usable cooling sites. Validate entrances, barriers and access
constraints before calling a route accessible. Transit-to-cooling itineraries
need service direction, transfers and walking legs.

Traffic-aware truck travel times can follow once candidate stops and depots are
defined. A historical traffic archive is optional for that first routing step.

**Acceptance:** actual network travel times for the selected dispatch locations,
with assumptions and unavailable routes explicit; walking/transit accessibility
is supported by route evidence rather than straight-line proximity.

## Recommended first milestone

Complete geography/Census alignment and the facility crosswalk, then publish a
validated enriched context and event-specific query layer for the agent. The
primary outputs are usable joins, verified facility readiness and reproducible
data-quality checks. Model benchmarking and routing follow those foundations.
