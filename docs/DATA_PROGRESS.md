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

## Priorities 4–6

In progress. Regional routing acquisition and model/event preparation were
prepared independently. Event-date validation initially
uses November 17, 2026 unless the team specifies another planning date; this
does not imply a heat event is occurring on that date.

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
```

The runner writes only to a separate working dataset and records completed
jobs. Its SQL guard rejects writes to original or backup namespaces. Local
source caches and recovery archives are excluded from Git.
