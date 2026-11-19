# Local backup and recovery (after the lab project expires)

The hackathon runs in a temporary Qwiklabs project (`qwiklabs-gcp-04-3e58e01275b8`). When it expires, its BigQuery
datasets, its in-project backup dataset (`a4i_climate_backup_20261117_214035`, see [DATA_RECOVERY.md](DATA_RECOVERY.md))
and its Cloud Storage buckets all disappear. The **local backup** below is the copy that survives.

## What the backup contains

Made by `scripts/export_bigquery_local.sh` into `data/backup/<UTC stamp>/` (git-ignored; copy it off the laptop,
e.g. to Google Drive). The 2026-11-18 backup is `data/backup/20261118T0840/` (148 MB, 0 problems):

| Dataset | Tables | Views | Notes |
|---|---:|---:|---|
| `a4i_climate` | 38 | 1 | **Original challenge tables** `heat_observations` (48,855 rows), `station_daily_temps` (25,891), `tract_demographics` (1,627), plus City facilities, OSM, grid, Landsat/NLCD, transit, weather, console approvals |
| `a4i_climate_work_v2` | 25 | 12 | Enriched data: ACS 2024, reconciled facilities, routing network, `site_context_enriched` |
| `a4i_scratch_chicago` | 3 | 0 | Scratch copies |

Per table: `<dataset>/<table>/part-*.json.gz` (gzip JSON lines, GEOGRAPHY as text) and `<dataset>/schemas/<table>.json`.
Per view: `<dataset>/views/<view>.sql`. Per model: `<dataset>/models/<model>.metadata.json` plus exported files.
`MANIFEST.tsv` lists every object with BigQuery vs downloaded row counts and a status.

**What is in git:** only the three original challenge tables (data + schema, ~0.9 MB) and `MANIFEST.tsv` are
committed (see the `data/backup/**` exceptions in `.gitignore`); they cannot be re-downloaded once the lab ends.
The rest (~146 MB: OSM, routing network, enriched tables, console approvals with approver names) stays local /
on Drive. A fresh clone can restore just the originals with the same command as Option B, pointing at
`data/backup/20261118T0840` (the script restores whatever schemas are present: 3 loads, 3 row checks, no views).

**Before sharing the backup:** federal data (NOAA, Census, USGS) is public domain; City of Chicago data is under the
City's open-data terms; OpenStreetMap is ODbL (attribute "© OpenStreetMap contributors"). `dispatch_approvals.actor`
and `dispatch_plan_versions.created_by` record who approved plans in the console: treat them as personal data.

## Make a fresh backup (while the lab project still exists)

```bash
CLOUDSDK_CONFIG=$PWD/.gcloud PROJECT=qwiklabs-gcp-04-3e58e01275b8 bash scripts/export_bigquery_local.sh
# then confirm: grep -c -E 'FAILED|MISMATCH' data/backup/<stamp>/MANIFEST.tsv   -> 0
```

## Option A: use the data without BigQuery

No cloud account needed:

```python
import pandas as pd, glob
heat = pd.concat(pd.read_json(f, lines=True) for f in glob.glob("data/backup/20261118T0840/a4i_climate/heat_observations/*.json.gz"))
```

```sql
-- DuckDB
SELECT * FROM read_json_auto('data/backup/20261118T0840/a4i_climate/station_daily_temps/*.json.gz');
```

## Option B: restore into a new BigQuery project

**Prerequisites:** a Google Cloud project with billing, the `bq`/`gcloud` CLIs logged in
(`gcloud auth login`; on this Mac prefix commands with `CLOUDSDK_CONFIG=$PWD/.gcloud`), BigQuery Data Editor and
Job User roles. Earth Engine is **not** needed: the satellite and land-cover results are already in the tables.

**1. Preview** (prints every command, changes nothing):

```bash
PROJECT=<new-project> bash scripts/restore_bigquery_local.sh data/backup/20261118T0840 --dry-run | less
```

Expected plan for the 2026-11-18 backup: 3 dataset creates, 86 loads (66 tables; 20 extra parts appended),
66 row-count checks, 13 views.

**2. Restore** datasets, tables and views:

```bash
PROJECT=<new-project> bash scripts/restore_bigquery_local.sh data/backup/20261118T0840
```

It creates each dataset in `US` if missing, loads every table with its saved schema (first part `--replace`, the
rest appended, because `bq` uploads one local file per load), checks each row count against `MANIFEST.tsv`, then
recreates the views with `qwiklabs-gcp-04-3e58e01275b8` rewritten to your project (up to 3 passes, so views built on
other views succeed). It ends with `>> restore complete` or lists every failure and exits non-zero.
Set `OLD_PROJECT` if the backup came from a different project, `BQ_LOCATION` for a non-US location.

**3. Retrain the models.** Models are not loaded back (an exported model returns as a TensorFlow model without
`ML.EXPLAIN_PREDICT` attributions); retraining from the restored tables is deterministic for these linear models:

```bash
# original model (sql/04) -> heat_offset_model
python3 -c "import sys; sys.path.insert(0,'scripts'); from run_sql import load_vars, render; \
print(render(open('sql/04_train_model.sql').read(), load_vars('<new-project>','a4i_climate')))" \
  | bq --project_id=<new-project> query --nouse_legacy_sql
# v2 model + its views (sql/v2) -> site_context_lst, heat_offset_trend_only, heat_offset_lst
PROJECT=<new-project> bash scripts/run_v2.sh build
PROJECT=<new-project> bash scripts/run_v2.sh evaluate     # expect held-out r2: previous 0.26, trend 0.50, v2 0.59
```

The view `a4i_climate_work_v2.site_context_enriched_lst` is restored in step 2 (or rebuild it with
`bash scripts/run_v2.sh render sql/v2/20_site_context_enriched_lst.sql | bq --project_id=<new-project> query --nouse_legacy_sql`).

**4. Point the app at the new project.** Set `GOOGLE_CLOUD_PROJECT=<new-project>` (and `.env`). The enriched
toolset reads "other" tables from a baseline dataset that defaulted to the lab's snapshot; in a restored project set
`BQ_BASELINE_DATASET=a4i_climate`. Then regenerate/deploy as usual, e.g.
`TOOLS_SRC=toolbox/tools.enriched.yaml bash scripts/deploy_toolbox.sh` (see docs/RUNBOOK.md for the agent and console).

## Verify

```bash
# row counts (step 2 already prints ok/MISMATCH per table); the three original tables:
bq --project_id=<new-project> query --nouse_legacy_sql \
 'SELECT (SELECT COUNT(*) FROM a4i_climate.heat_observations) heat, (SELECT COUNT(*) FROM a4i_climate.station_daily_temps) stations,
         (SELECT COUNT(*) FROM a4i_climate.tract_demographics) tracts'   # expect 48855, 25891, 1627
make test                                                                 # offline suite, no cloud needed
```

## Known limits

- The Toolbox, agent and console must be redeployed in the new project; their URLs and service accounts change.
- Table options (partitioning, descriptions, labels) are not carried over; schemas and data are.
- `site_context_lst` and the enriched view are views: they need their source tables restored first (step 2 does this).
