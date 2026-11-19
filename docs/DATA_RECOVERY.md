# Data recovery baseline

> **After the hackathon:** this in-project backup disappears with the lab project. To keep the data, use the local
> backup and restore steps in [LOCAL_BACKUP_RECOVERY.md](LOCAL_BACKUP_RECOVERY.md).

## Verified baseline

Source: `qwiklabs-gcp-04-3e58e01275b8.a4i_climate`.

Table baseline: **November 17, 2026, 13:40:35 PST**
(`2026-11-17T21:40:35.750513Z`). All table snapshots use this same timestamp.
The four models were copied immediately afterward; model copies do not use
the table time-travel timestamp.

Backup dataset:
`qwiklabs-gcp-04-3e58e01275b8.a4i_climate_backup_20261117_214035`.

- **38 read-only table snapshots**, with no expiration, in the US region.
- **Four model copies**: `heat_offset_model`, `heat_offset_v2`,
  `heat_offset_v3`, and `exp_bt_phys`.
- **1,547,308 table rows** archived locally as gzip JSONL, with schemas,
  source/snapshot metadata, SHA-256 hashes and job IDs.
- Local compressed table data totals **41,212,242 bytes** (about 41 MB).
- Local archive restores passed row-count, schema and data-checksum checks for
  `heat_observations` (48,855 rows), `community_areas` (77) and
  `weather_hourly` (936).
- A snapshot restore of **`high_schools` passed with all 187 rows**, matching
  schema and data checksums.
- All 38 snapshot types, expiration settings, row counts and local archive
  hashes were checked again after export.

The four restore-test tables remain in the backup dataset. No source table or
source model was replaced by these operations. Priorities 1–6 in
[DATA_WORK_PLAN.md](DATA_WORK_PLAN.md) have not begun.

## Where the local files are

Repository-relative directory: `data/enrichment/recovery/20261117_214035/`.

- `manifest.json`: snapshot destinations, row counts, schemas, archive hashes,
  model destinations and successful restore checks.
- `tables/<table>.jsonl.gz`: every table's data, including an empty archive for
  each empty table.
- `tables/<table>.metadata.json`: original and snapshot table metadata.
- `models/<model>.metadata.json`: original and copied model metadata.
- `source_dataset.metadata.json`: original dataset settings.

`data/enrichment/recovery/latest.json` points to this verified manifest.
These archives are local and git-ignored. They should be copied to durable
team storage before deleting this workstation checkout or the lab project.
The local table archives remain usable after the Qwiklabs project is deleted;
the BigQuery snapshots and model copies do not. Local model files contain
metadata, **not exported trained-model artifacts**.

## Restore a snapshot to a new table

The tested method uses BigQuery's RESTORE copy operation. Restore into a new
table name, then validate it before redirecting application queries. The
following example uses a destination that was not created by the baseline run.
Run from the repository root with the active signed-in `gcloud` account:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path("scripts").resolve()))
from load_enrichment_snapshots import gcloud_credentials
from google.cloud.bigquery.client import Client
from google.cloud.bigquery.job.copy_ import CopyJobConfig

project = "qwiklabs-gcp-04-3e58e01275b8"
client = Client(project=project, location="US", credentials=gcloud_credentials())
client.copy_table(
    f"{project}.a4i_climate_backup_20261117_214035.high_schools",
    f"{project}.a4i_climate.high_schools_recovered_20261117",
    job_config=CopyJobConfig(operation_type="RESTORE", write_disposition="WRITE_EMPTY"),
).result()
```

For a copied model, use `client.copy_table` with `CopyJobConfig` containing
`write_disposition="WRITE_EMPTY"` and no RESTORE operation. BigQuery detects
the model source. Keep the destination model name new.

## Restore a local archive

Create a destination dataset first if the lab project no longer exists. Use its
project and dataset IDs as the destination, with suitable credentials. The
example below restores the row data and schema; original table options such as
partitioning, clustering and descriptions are recorded in the metadata files
and can be reapplied separately.

```python
import gzip
import io
import json
from pathlib import Path

from google.cloud.bigquery.job.load import LoadJobConfig
from google.cloud.bigquery.schema import SchemaField
from backup_bigquery import sha256_file

archive = Path("data/enrichment/recovery/20261117_214035")
manifest = json.loads((archive / "manifest.json").read_text())
entry = next(item for item in manifest["tables"] if item["name"] == "high_schools")
data_path = archive / "tables/high_schools.jsonl.gz"
if sha256_file(data_path) != entry["sha256"]:
    raise ValueError("Archive hash mismatch")
metadata = json.loads((archive / "tables/high_schools.metadata.json").read_text())
schema = [
    SchemaField.from_api_repr(field)
    for field in metadata["snapshot_metadata"]["schema"]["fields"]
]
destination = f"{project}.a4i_climate.high_schools_from_archive_20261117"
payload = gzip.decompress(data_path.read_bytes())
if payload:
    client.load_table_from_file(
        io.BytesIO(payload), destination,
        job_config=LoadJobConfig(
            schema=schema, source_format="NEWLINE_DELIMITED_JSON",
            write_disposition="WRITE_EMPTY",
        ),
    ).result()
else:
    from google.cloud.bigquery.table import Table
    client.create_table(Table(destination, schema=schema))
if client.get_table(destination).num_rows != entry["rows"]:
    raise ValueError("Restored row count mismatch")
```

The baseline script's `restore_probe` also compares schemas and order-independent
row checksums against the BigQuery snapshot. A full rollback should validate
the chosen recovered tables and agent queries before switching consumers.

## Future baseline runs

```bash
.venv/bin/python scripts/backup_bigquery.py \
  --project qwiklabs-gcp-04-3e58e01275b8 --dataset a4i_climate
```

Every run creates a new dated backup dataset. The script does not overwrite
existing backups or implement any enrichment transformations.
