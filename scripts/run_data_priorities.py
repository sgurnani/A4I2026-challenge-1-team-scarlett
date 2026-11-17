#!/usr/bin/env python3
"""Build validated enrichment outputs in a separate BigQuery working dataset.

Source tables/models are never write targets. Each completed job and validation
is recorded locally; subsequent priorities can reuse completed outputs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backup_bigquery import json_value, save_json
from load_enrichment_snapshots import gcloud_credentials
from run_sql import render

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql/enrichment"
DATA_DIR = ROOT / "data/enrichment/priorities"
BOUNDARY_SCHEMA = [("geo_id", "STRING"), ("name", "STRING"), ("acs_year", "INTEGER"),
                   ("geojson", "STRING"), ("source", "STRING")]


def identifier(value: str, project: bool = False) -> str:
    pattern = r"[a-z][a-z0-9-]{4,61}[a-z0-9]" if project else r"[A-Za-z_][A-Za-z0-9_]{0,1023}"
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid BigQuery identifier: {value}")
    return value


def validate_targets(sql: str, project: str, work_dataset: str) -> None:
    """Reject destructive SQL and writes outside the working dataset."""
    if re.search(r"\b(DROP|TRUNCATE|DELETE|UPDATE|MERGE)\b", sql, re.I):
        raise ValueError("Destructive SQL is not supported by this runner")
    targets = re.findall(
        r"\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|MODEL|VIEW)(?:\s+IF\s+NOT\s+EXISTS)?|INSERT\s+INTO)\s+`([^`]+)`",
        sql, re.I,
    )
    if any(not target.startswith(f"{project}.{work_dataset}.") for target in targets):
        raise ValueError("SQL write target is outside the working dataset")


class WorkRunner:
    def __init__(self, project: str, source_dataset: str, work_dataset: str,
                 output_dir: Path = DATA_DIR) -> None:
        from google.cloud.bigquery.client import Client
        from google.cloud.bigquery.dataset import Dataset

        self.project = identifier(project, project=True)
        self.source = identifier(source_dataset)
        self.work = identifier(work_dataset)
        if self.work == self.source or self.work == "a4i_climate" or "backup" in self.work:
            raise ValueError("Working dataset must be separate from originals and backups")
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = output_dir / "progress.json"
        self.report: dict[str, Any] = {"project": project, "source_dataset": source_dataset,
                                      "work_dataset": work_dataset, "jobs": [], "priorities": {}}
        if self.report_path.exists():
            existing = json.loads(self.report_path.read_text())
            if any(existing[k] != self.report[k] for k in ("project", "source_dataset", "work_dataset")):
                raise ValueError("Progress file belongs to a different source/destination")
            self.report = existing
        credentials = gcloud_credentials()
        client = Client(project=project, credentials=credentials)
        source = client.get_dataset(f"{project}.{source_dataset}")
        self.client = Client(project=project, location=source.location, credentials=credentials)
        dataset = Dataset(f"{project}.{work_dataset}")
        dataset.location = source.location
        dataset.description = f"Versioned enrichment outputs; source {project}.{source_dataset} remains unchanged"
        dataset.labels = {"purpose": "validated_enrichment"}
        self.client.create_dataset(dataset, exists_ok=True)
        self.variables = {"PROJECT": project, "SOURCE": source_dataset, "WORK": work_dataset}

    def query(self, label: str, sql: str, extra: dict | None = None) -> list[dict]:
        from google.cloud.bigquery.job.query import QueryJobConfig

        statement = render(sql, {**self.variables, **(extra or {})})
        validate_targets(statement, self.project, self.work)
        job = self.client.query(statement, job_config=QueryJobConfig(maximum_bytes_billed=5_000_000_000))
        rows = [dict(row.items()) for row in job.result()]
        self.report["jobs"].append({"label": label, "job_id": job.job_id,
                                    "bytes_processed": job.total_bytes_processed,
                                    "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                    "result": json_value(rows)})
        save_json(self.report_path, self.report)
        print(f"Verified job: {label}", flush=True)
        return rows

    def sql(self, name: str, extra: dict | None = None) -> list[dict]:
        return self.query(name, (SQL_DIR / name).read_text(), extra)

    def load(self, name: str, rows: list[dict], schema: list[tuple[str, str]]) -> None:
        from google.cloud.bigquery.job.load import LoadJobConfig
        from google.cloud.bigquery.schema import SchemaField
        from google.cloud.bigquery.table import Table

        identifier(name)
        destination = f"{self.project}.{self.work}.{name}"
        fields = [SchemaField(field, kind) for field, kind in schema]
        if not rows:
            self.client.create_table(Table(destination, schema=fields), exists_ok=True)
            return
        job = self.client.load_table_from_json(rows, destination, job_config=LoadJobConfig(
            schema=fields, write_disposition="WRITE_TRUNCATE"))
        job.result()
        table = self.client.get_table(destination)
        if table.num_rows != len(rows):
            raise ValueError(f"Load row mismatch: {destination}")
        digest = hashlib.sha256(json.dumps(json_value(rows), sort_keys=True, allow_nan=False).encode()).hexdigest()
        self.report["jobs"].append({"label": f"load_{name}", "job_id": job.job_id,
                                    "destination": destination, "rows": len(rows), "sha256": digest})
        save_json(self.report_path, self.report)
        print(f"Loaded {name}: {len(rows)} rows", flush=True)

    def complete(self, priority: str, evidence: Any) -> None:
        self.report["priorities"][priority] = {"status": "validated",
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "evidence": json_value(evidence)}
        save_json(self.report_path, self.report)

    def load_file(self, name: str, path: Path, schema: list[tuple[str, str]],
                  write_disposition: str = "WRITE_TRUNCATE") -> int:
        from google.cloud.bigquery.job.load import LoadJobConfig
        from google.cloud.bigquery.schema import SchemaField
        from google.cloud.bigquery.table import Table

        identifier(name)
        destination = f"{self.project}.{self.work}.{name}"
        fields = [SchemaField(field, kind) for field, kind in schema]
        expected = {field for field, _ in schema}
        count = 0
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for raw in handle:
                digest.update(raw)
                row = json.loads(raw)
                if set(row) != expected:
                    raise ValueError(f"Schema mismatch in {path}")
                count += 1
        if not count:
            self.client.create_table(Table(destination, schema=fields), exists_ok=True)
            return 0
        with path.open("rb") as handle:
            job = self.client.load_table_from_file(handle, destination, job_config=LoadJobConfig(
                schema=fields, source_format="NEWLINE_DELIMITED_JSON", write_disposition=write_disposition))
        job.result()
        if job.output_rows != count:
            raise ValueError(f"Load output count mismatch for {destination}")
        self.report["jobs"].append({"label": f"load_{name}", "job_id": job.job_id,
                                    "rows": count, "sha256": digest.hexdigest(), "file": str(path)})
        save_json(self.report_path, self.report)
        print(f"Loaded {name}: {count} rows", flush=True)
        return count

    def geography(self) -> None:
        raw = ROOT / "data/enrichment/geography/raw"
        rows = []
        for path in sorted(raw.glob("tracts_*.geojson")):
            for feature in json.loads(path.read_text())["features"]:
                props = feature["properties"]
                rows.append({"geo_id": props["GEOID"], "name": props["NAME"], "acs_year": 2024,
                             "geojson": json.dumps(feature["geometry"], separators=(",", ":")),
                             "source": "Census TIGERweb tigerWMS_ACS2024/MapServer/8; Cook County"})
        if len(rows) != 1332 or len({row["geo_id"] for row in rows}) != len(rows):
            raise ValueError("Expected all 1332 unique cached Cook County tract polygons")
        self.load("tract_boundaries_2024", rows, BOUNDARY_SCHEMA)
        city = json.loads((raw / "chicago_census_place.geojson").read_text())["features"][0]
        self.load("chicago_boundary_2024", [{
            "geo_id": "1714000", "name": "Chicago city", "acs_year": 2024,
            "geojson": json.dumps(city["geometry"], separators=(",", ":")),
            "source": "Census TIGERweb tigerWMS_ACS2024/MapServer/28",
        }], BOUNDARY_SCHEMA)
        evidence = self.sql("01_cell_tract_map.sql")
        self.report["geography"] = json_value(evidence)
        save_json(self.report_path, self.report)

    def census(self) -> None:
        from load_chicago_acs_aligned import SCHEMA

        path = ROOT / "data/enrichment/acs_aligned/tract_acs_2024_cook.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        self.load("tract_acs_2024", rows, SCHEMA)
        evidence = self.sql("01_tract_vulnerability.sql")
        self.complete("1", {"geography": self.report["geography"], "census": evidence})

    def facilities(self) -> None:
        from reconcile_facilities import SCHEMAS

        if "1" not in self.report["priorities"]:
            raise ValueError("Validate priority 1 before publishing priority 2")
        directory = ROOT / "data/enrichment/reconciled_facilities"
        for name, schema in SCHEMAS.items():
            rows = [json.loads(line) for line in (directory / f"{name}.jsonl").read_text().splitlines() if line.strip()]
            # Source refreshes must never replace operator confirmations.
            destination = "facility_readiness_seed" if name == "facility_readiness" else name
            self.load(destination, rows, schema)
        evidence = self.sql("02_facilities.sql")
        self.complete("2", {"data_validated": evidence, "operator_confirmations_pending": True})

    def context(self) -> None:
        if "2" not in self.report["priorities"]:
            raise ValueError("Validate priorities 1 and 2 before context publication")
        evidence = self.sql("03_site_context.sql")
        self.complete("3", {"context": evidence, "production_consumer_switched": False})

    def event(self, event_date: str = "2026-11-17") -> None:
        from prepare_event_data import SCHEMAS

        if "3" not in self.report["priorities"]:
            raise ValueError("Validate priority 3 before event publication")
        dt.date.fromisoformat(event_date)
        runs = sorted((ROOT / "data/enrichment/event" / event_date).glob("*/manifest.json"))
        if not runs:
            raise ValueError("Prepare event data before publishing it")
        directory = runs[-1].parent
        # Overall validity is the commit marker: publish it after all run data.
        names = [name for name in SCHEMAS if name != "event_data_validity"] + ["event_data_validity"]
        for name in names:
            self.load_file(name + "_history", directory / f"{name}.jsonl", SCHEMAS[name], "WRITE_APPEND")
        evidence = self.sql("04_event_data.sql", {"EVENT_DATE": event_date})
        self.complete("4", {"event_date": event_date, "event_run": str(directory), "event": evidence})

    def routing(self) -> None:
        from load_chicago_routing import SCHEMAS

        if "5" not in self.report["priorities"]:
            raise ValueError("Validate priority 5 before priority 6 publication")
        directory = ROOT / "data/enrichment/routing_citywide"
        manifest = json.loads((directory / "manifest.json").read_text())
        for name, schema in SCHEMAS.items():
            count = self.load_file(name, directory / f"{name}.jsonl", schema)
            if count != manifest["row_counts"][name]:
                raise ValueError(f"Routing source count mismatch: {name}")
        self.load("routing_source", [{"source": manifest["source"], "retrieved_at": manifest["retrieved_at"],
                  "source_last_modified": manifest["source_last_modified"], "source_sha256": manifest["sha256"],
                  "license": manifest["license"], "attribution": manifest["attribution"],
                  "manifest_json": json.dumps(manifest)}], [("source", "STRING"), ("retrieved_at", "TIMESTAMP"),
                  ("source_last_modified", "STRING"), ("source_sha256", "STRING"), ("license", "STRING"),
                  ("attribution", "STRING"), ("manifest_json", "STRING")])
        evidence = self.sql("06_routing_network.sql")
        self.report["routing_network"] = json_value(evidence)
        save_json(self.report_path, self.report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["geography", "census", "facilities", "context", "event", "routing"])
    parser.add_argument("--event-date", default="2026-11-17")
    parser.add_argument("--project", default="qwiklabs-gcp-04-3e58e01275b8")
    parser.add_argument("--source-dataset", default="a4i_climate_backup_20261117_214035")
    parser.add_argument("--work-dataset", default="a4i_climate_work_v2")
    args = parser.parse_args()
    runner = WorkRunner(args.project, args.source_dataset, args.work_dataset)
    if args.stage == "event":
        runner.event(args.event_date)
    else:
        getattr(runner, args.stage)()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
