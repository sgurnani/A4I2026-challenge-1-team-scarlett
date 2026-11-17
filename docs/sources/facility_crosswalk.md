# Facility reconciliation and readiness

## Prepared artifacts

`scripts/reconcile_facilities.py` reads the existing BigQuery facility inventories,
then writes local reconciled snapshots. It has no cloud write path and does not contact operators.
`SCHEMAS` exports explicit BigQuery field/type pairs for three output tables:

| File under `data/enrichment/reconciled_facilities/` | Rows | Purpose |
|---|---:|---|
| `canonical_facilities.jsonl` | 995 | Canonical inventory identities, source memberships, coordinate provenance and conservative classification |
| `facility_source_crosswalk.jsonl` | 2,463 | Every input row linked to a canonical identity, with original fields and claims preserved |
| `facility_readiness.jsonl` | 995 | One explicitly unconfirmed seed per facility; operational facts are null |
| `reconciliation_report.json` | — | Source counts, unresolved identities, discrepancies and validation results |
| `source_vintages.json` | 6 sources | Public source metadata verified on 2026-11-17 |

The inputs, downloaded read-only on 2026-11-17 from
`qwiklabs-gcp-04-3e58e01275b8.a4i_climate`, are:

| Source table | Rows |
|---|---:|
| `chi_site_cps_schools` | 645 |
| `chi_site_libraries` | 82 |
| `chi_site_police_stations` | 23 |
| `chi_site_senior_centers` | 21 |
| `cooling_inventory_snapshot` | 287 |
| `high_schools` | 187 |
| `chi_site_cooling_centers` | 287 |
| `chi_site_candidate_sites` | 457 |
| `cooling_facilities` | 474 |

Raw source snapshots and table schemas are retained under ignored
`.data/reconciled_facility_sources/`. Output crosswalk rows retain the original source row as JSON,
source dataset ID, source retrieval time, source table and source-specific record identity.

## Identity and location rules

Official CPS school IDs anchor `cps-school-ID` canonical identities. Different school IDs remain
different entities even if they share a campus, address or name. Library location IDs and police
district IDs are used where available. Sources lacking official IDs receive deterministic hashes
of normalized type, name and address. Those fallback IDs are stable under source row reordering;
if both name and address change, the crosswalk needs a deliberate identity migration.

Supplementary rows are matched by official school ID, exact normalized name/address, or a
unique compatible facility at the same normalized address within 250 metres. Ambiguous matches
remain separate records marked `identity_review_required`; no fuzzy matching is performed.
Every source row is preserved, including rows without a school ID or usable coordinates.

The imported newer CPS coordinates are preferred for matching school IDs. Library, police and
senior-center coordinates use their dedicated inventory where matched. Source coordinates are
retained in the crosswalk. A difference greater than 250 metres is flagged, not averaged away.

The snapshot contains **106 legacy school records with coordinate discrepancies** and
**101 corresponding appended cooling-facility rows**, across 106 canonical facilities.
There are also 44 source address disagreements. These are differences between source datasets;
they do not constitute a new geocoding survey or verification of the chosen coordinate.

## School coverage and unresolved records

All **187 legacy high-school rows survive**, as 187 distinct linked facility IDs:

- 152 match IDs in the imported newer CPS school inventory.
- 150 match its 170 high-school candidate records.
- 2 match educational units classified as elementary in the newer source; the legacy membership
  and the source grade disagreement both remain visible.
- 35 do not match the newer CPS inventory: 33 nonempty IDs and 2 rows without a school ID.
- 20 of the newer source's 170 high-school candidates have no legacy row.

The unresolved 35 schools are listed by ID, name and address in
`legacy_high_schools_without_current_cps_match_details` in the reconciliation report.
Another 10 appended `cooling_facilities` school aliases lack a unique identity match; these remain
separate unverified inventory records and are listed in `identity_review_required_details`.

Canonical membership fields make the populations explicit:

- `is_current_cps_school`: membership in the imported 645-row newer CPS source.
- `is_current_cps_high_school`: 170 educational units classified as HS in that source.
- `is_current_high_school_candidate`: membership in the 170-row CPS candidate subset.
- `is_legacy_high_school`: membership in the 187-row legacy source.
- `candidate_status`: the source-based candidacy role, with no operational readiness implied.

The remaining 475 CPS units are not high-school candidates. See the source-vintage section below
for the meaning of “current” in these inherited source names and membership fields.

## Cooling classification and readiness

**134 canonical facilities** are screened into the designated indoor inventory access subset.
`is_indoor_inventory_access_candidate` is true only for a City cooling-inventory member whose
published category is screened as an indoor site. This subset is suitable for a map of potential
indoor access gaps. It does not establish that a site is open, staffed, accessible or cooling properly.

All **43 Park District Facility records** remain `physical_setting=unknown` and
`indoor_cooling_classification=unconfirmed`. The earlier inventory classified those rows as
`is_indoor=false`, while another import assigned `indoor_air_conditioned=true`. Both original claims
are retained with provenance; the conflict is resolved operationally by leaving readiness unknown.
The 110 spray features are classified `outdoor_only` and excluded from indoor access.

Legacy high-school imports contain generated `has_ac_gym=true` values and the text
“Mon-Fri 7AM-7PM (Emergency AC Gymnasium Cooling).” Those are assumptions from
`scripts/load_chicago_high_schools.py`, not observations supplied by the school-location dataset.
They remain unverified source claims and never become verified AC or opening facts.

Every readiness seed has `readiness_status=unconfirmed` and null event date, capacity, AC,
opening, staffing, wheelchair access, verifier and verification time. Source inventory presence
does not fabricate an operator confirmation. When published by the parent workflow, these are
**missing-record seeds only**: existing operator confirmations must not be truncated or replaced
by a refreshed inventory snapshot.

## Source vintage and freshness

Live Socrata metadata was checked separately on 2026-11-17:

| Public dataset | Metadata period | Interpretation |
|---|---|---|
| `c7jj-qjvh` — CPS schools | 2013–2014 academic year | Legacy inventory; last source row update in 2013 |
| `pb6d-zzuh` — CPS school locations | July 2025–June 2026 | SY2025–2026, newer than legacy; not verified SY2026–2027 operation |
| `x8fc-8rcq` — Libraries | Updated as needed | Last source row update in September 2026; usual hours and temporary closures can differ |
| `z8bn-74gv` — Police stations | “Current as of 2011” | Last source row update in 2016 |
| `qhfc-4cw2` — Senior centers | “Current as of 2011” | Last source row update in 2019 |
| `msrk-w9ih` — Cooling Centers | Summer 2024 | Last source row update in July 2026; some sites activate only in extreme heat |

The `is_current_cps_*` and candidate fields denote membership in the imported **newer snapshot
relative to the legacy source**. They do not verify that a school still operates today. Fetching
a currently published endpoint or refreshing a BigQuery table does not change the source's vintage.

Metadata URLs and exact row-update times are preserved in `source_vintages.json`. The library
source explicitly recommends its [current location directory](https://chipublib.bibliocommons.com/locations)
and [closure notices](https://www.chipublib.org/news/cpl-location-closures) for up-to-date opening.
The cooling source says some sites activate only during extreme heat and suggests 311 for current
operation. No calls, email or other operator contact were made.

## Reproduction and checks

Recompute from the downloaded inputs:

```bash
.venv/bin/python scripts/reconcile_facilities.py
```

Acquire a new read-only source snapshot, then reconcile locally:

```bash
.venv/bin/python scripts/reconcile_facilities.py --fetch \
  --project qwiklabs-gcp-04-3e58e01275b8 --dataset a4i_climate
```

The active gcloud account is used through `gcloud_credentials`; tokens stay in memory.
The parser validates project and dataset names. There is no `--load` option.

Eight focused tests cover preservation of 187 schools and 35 unresolved rows, distinct IDs on
shared campuses, 106 coordinate discrepancies, 43 uncertain parks, elementary versus high-school
membership, ambiguous addresses, fallback identity stability and invalid-coordinate preservation.
The script also validates all source row counts, unique canonical/source IDs, foreign-key coverage,
readiness coverage and exact schema fields before writing outputs. Mypy passes for the new script.

Sources retain City of Chicago attribution and portal terms. Public metadata can be inspected at
`https://data.cityofchicago.org/api/views/DATASET_ID.json`; these snapshots use aggregate public
facility records and contain no individual resident data.
