"""Persistence for plan versions and the approvals audit log.

MemoryStore for tests and local runs; BigQueryStore when deployed. The web service account may only insert into and
read these two tables (table-level roles/bigquery.dataEditor) and run query jobs; it cannot read the rest of the
dataset. Tables are created by scripts/deploy_web.sh from web/backend/schema/*.json.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

VERSIONS = "dispatch_plan_versions"
APPROVALS = "dispatch_approvals"


class Store(Protocol):
    async def save_version(self, row: dict) -> None: ...
    async def log_events(self, rows: list[dict]) -> None: ...
    async def latest(self) -> dict | None: ...
    async def clear(self) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self.versions: list[dict] = []
        self.events: list[dict] = []

    async def save_version(self, row: dict) -> None:
        self.versions.append(row)

    async def log_events(self, rows: list[dict]) -> None:
        self.events.extend(rows)

    async def latest(self) -> dict | None:
        return json.loads(self.versions[-1]["payload"]) if self.versions else None

    async def clear(self) -> None:
        self.versions.clear()
        self.events.clear()


class BigQueryStore:
    def __init__(self, project: str, dataset: str) -> None:
        from google.cloud import bigquery

        self.bq = bigquery.Client(project=project)
        self.versions = f"{project}.{dataset}.{VERSIONS}"
        self.approvals = f"{project}.{dataset}.{APPROVALS}"

    async def _insert(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        errors = await asyncio.to_thread(self.bq.insert_rows_json, table, rows)
        if errors:
            raise RuntimeError(f"BigQuery insert into {table} failed: {errors[:3]}")

    async def save_version(self, row: dict) -> None:
        await self._insert(self.versions, [row])

    async def log_events(self, rows: list[dict]) -> None:
        await self._insert(self.approvals, rows)

    async def latest(self) -> dict | None:
        sql = f"SELECT payload FROM `{self.versions}` ORDER BY version DESC, created_at DESC LIMIT 1"

        def run() -> Any:
            return list(self.bq.query(sql).result())

        rows = await asyncio.to_thread(run)
        return json.loads(rows[0]["payload"]) if rows else None

    async def clear(self) -> None:
        def run() -> None:
            for table in (self.versions, self.approvals):
                try:
                    self.bq.query(f"TRUNCATE TABLE `{table}`").result()
                except Exception:
                    self.bq.query(f"DELETE FROM `{table}` WHERE TRUE").result()

        await asyncio.to_thread(run)

