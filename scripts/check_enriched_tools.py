#!/usr/bin/env python3
"""Execute each enriched read-only tool SQL against BigQuery and save evidence."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "toolbox"))

from backup_bigquery import json_value, save_json
from load_enrichment_snapshots import gcloud_credentials
from render_toolbox import resolve
import build_enriched_tools_yaml


def main() -> int:
    from google.cloud.bigquery.client import Client
    from google.cloud.bigquery.job.query import QueryJobConfig
    from google.cloud.bigquery.query import ScalarQueryParameter

    project = "qwiklabs-gcp-04-3e58e01275b8"
    client = Client(project=project, location="US", credentials=gcloud_credentials())
    docs = [doc for doc in build_enriched_tools_yaml.build() if doc["kind"] == "tool"]
    defaults: dict[str, Any] = {"latitude": 41.8781, "longitude": -87.6298, "limit": 2,
                                "event_date": "2026-11-17", "cell_id": "13959_-21908", "mode": "walking"}
    types = {"integer": "INT64", "float": "FLOAT64", "string": "STRING", "boolean": "BOOL"}

    def check(doc: dict) -> dict:
        parameters = []
        for parameter in doc["parameters"]:
            value = defaults.get(parameter["name"], parameter.get("default"))
            if value is None:
                raise ValueError(f"Provide a value for {doc['name']}.{parameter['name']}")
            parameters.append(ScalarQueryParameter(parameter["name"], types[parameter["type"]], value))
        sql = resolve(doc["statement"], {"GOOGLE_CLOUD_PROJECT": project})
        job = client.query(sql, job_config=QueryJobConfig(query_parameters=parameters,
                                                         maximum_bytes_billed=1_000_000_000))
        rows = [dict(row.items()) for row in job.result()]
        result = {"tool": doc["name"], "job_id": job.job_id, "rows": len(rows),
                  "bytes_processed": job.total_bytes_processed,
                  "sample": json_value(rows[:1]), "status": "passed"}
        if doc["name"] == "list_sites":
            if len({row["site_key"] for row in rows}) != len(rows):
                raise ValueError("list_sites multiplied facility rows")
        print(f"Passed: {doc['name']} ({len(rows)} rows)", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(check, docs))
    save_json(ROOT / "data/enrichment/priorities/enriched_tool_checks.json", results)
    print(f"Verified {len(results)} enriched tools", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
