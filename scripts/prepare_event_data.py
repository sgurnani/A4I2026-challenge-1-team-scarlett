#!/usr/bin/env python3
"""Prepare event-date CTA schedules and NWS forecast windows from cached sources.

No network calls or cloud writes. Each run writes a new date/run directory and
keeps existing event snapshots. GTFS uses its noon-minus-12-elapsed-hours clock.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from load_chicago_transit import URL as TRANSIT_URL
from load_chicago_transit import normalize_feed
from load_chicago_weather import HOURLY_SCHEMA, POINTS

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
CHICAGO = ZoneInfo("America/Chicago")
SCHEMAS = {
    "event_transit_departures": [
        ("event_date", "DATE"), ("service_date", "DATE"), ("stop_id", "STRING"), ("route_id", "STRING"),
        ("service_hour", "INT64"), ("actual_local_date", "DATE"), ("actual_local_hour", "INT64"),
        ("local_fold", "INT64"), ("bucket_start_utc", "TIMESTAMP"), ("bucket_end_utc", "TIMESTAMP"),
        ("first_departure_utc", "TIMESTAMP"), ("last_departure_utc", "TIMESTAMP"),
        ("first_departure_local", "STRING"), ("last_departure_local", "STRING"),
        ("scheduled_departures", "INT64"), ("first_departure_seconds", "INT64"),
        ("last_departure_seconds", "INT64"), ("is_previous_service_date", "BOOL"),
        ("timezone", "STRING"), ("data_kind", "STRING"), ("source", "STRING"),
        ("source_retrieved_at", "TIMESTAMP"), ("prepared_at", "TIMESTAMP"),
    ],
    "event_transit_validity": [
        ("event_date", "DATE"), ("previous_service_date", "DATE"), ("timezone", "STRING"),
        ("event_status", "STRING"), ("event_service_active", "BOOL"), ("previous_service_active", "BOOL"),
        ("schedule_start", "DATE"), ("schedule_end", "DATE"),
        ("event_departure_rows", "INT64"), ("previous_day_spillover_rows", "INT64"),
        ("scheduled_departures_total", "INT64"), ("source_retrieved_at", "TIMESTAMP"),
        ("reference_time", "TIMESTAMP"), ("source_age_hours", "FLOAT64"), ("freshness_status", "STRING"),
        ("max_source_age_hours", "FLOAT64"), ("source_sha256", "STRING"),
        ("service_date_details_json", "STRING"), ("data_kind", "STRING"), ("prepared_at", "TIMESTAMP"),
    ],
    "event_weather_hourly": HOURLY_SCHEMA + [
        ("event_date", "DATE"), ("is_daytime_window", "BOOL"), ("is_overnight_window", "BOOL"),
        ("local_fold", "INT64"), ("data_kind", "STRING"), ("prepared_at", "TIMESTAMP"),
    ],
    "event_weather_summary": [
        ("event_date", "DATE"), ("grid_key", "STRING"), ("point_names", "STRING"),
        ("latitude", "FLOAT64"), ("longitude", "FLOAT64"), ("timezone", "STRING"),
        ("coverage_status", "STRING"), ("source_valid_start", "TIMESTAMP"), ("source_valid_end", "TIMESTAMP"),
        ("civil_day_start", "TIMESTAMP"), ("civil_day_end", "TIMESTAMP"),
        ("civil_day_expected_hours", "FLOAT64"), ("civil_day_covered_hours", "FLOAT64"),
        ("civil_day_missing_hours", "FLOAT64"), ("civil_day_complete", "BOOL"),
        ("forecast_high_f", "FLOAT64"), ("forecast_low_f", "FLOAT64"), ("available_civil_day_max_f", "FLOAT64"),
        ("daytime_start", "TIMESTAMP"), ("daytime_end", "TIMESTAMP"),
        ("daytime_expected_hours", "FLOAT64"), ("daytime_covered_hours", "FLOAT64"),
        ("daytime_missing_hours", "FLOAT64"), ("daytime_complete", "BOOL"),
        ("daytime_high_f", "FLOAT64"), ("available_daytime_max_f", "FLOAT64"),
        ("overnight_start", "TIMESTAMP"), ("overnight_end", "TIMESTAMP"),
        ("overnight_expected_hours", "FLOAT64"), ("overnight_covered_hours", "FLOAT64"),
        ("overnight_missing_hours", "FLOAT64"), ("overnight_complete", "BOOL"),
        ("overnight_low_f", "FLOAT64"), ("available_overnight_min_f", "FLOAT64"),
        ("forecast_issued_at", "TIMESTAMP"), ("forecast_generated_at", "TIMESTAMP"),
        ("source_retrieved_at", "TIMESTAMP"), ("source", "STRING"),
        ("reference_time", "TIMESTAMP"), ("source_age_hours", "FLOAT64"), ("freshness_status", "STRING"),
        ("max_source_age_hours", "FLOAT64"), ("hourly_rows_in_event_window", "INT64"),
        ("duplicate_rows_removed", "INT64"), ("older_snapshot_rows_excluded", "INT64"),
        ("data_kind", "STRING"), ("prepared_at", "TIMESTAMP"),
    ],
    "event_data_validity": [
        ("event_date", "DATE"), ("timezone", "STRING"), ("reference_time", "TIMESTAMP"),
        ("weather_status", "STRING"), ("weather_grid_count", "INT64"), ("weather_expected_grid_count", "INT64"),
        ("weather_fresh_grid_count", "INT64"), ("civil_day_complete_grid_count", "INT64"),
        ("daytime_complete_grid_count", "INT64"), ("overnight_complete_grid_count", "INT64"),
        ("can_publish_civil_day_forecast_high", "BOOL"), ("can_publish_daytime_high", "BOOL"),
        ("can_publish_overnight_low", "BOOL"), ("weather_retrieved_at_min", "TIMESTAMP"),
        ("weather_retrieved_at_max", "TIMESTAMP"), ("transit_status", "STRING"),
        ("transit_freshness_status", "STRING"), ("transit_source_retrieved_at", "TIMESTAMP"),
        ("transit_departure_rows", "INT64"), ("event_readiness_confirmed", "BOOL"),
        ("daytime_definition", "STRING"), ("overnight_definition", "STRING"),
        ("limitations_json", "STRING"), ("prepared_at", "TIMESTAMP"),
    ],
}


def aware_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(UTC)


def freshness(retrieved_at: str | None, reference: dt.datetime, max_age_hours: float) -> tuple[float | None, str]:
    if retrieved_at is None:
        return None, "unknown_source_retrieval"
    age = (reference - aware_time(retrieved_at)).total_seconds() / 3600
    if age < 0:
        return age, "retrieved_after_reference"
    return age, "fresh" if age <= max_age_hours else "stale"


def gtfs_time(service_date: dt.date, seconds: int) -> dt.datetime:
    """GTFS origin: local noon minus twelve elapsed hours, then elapsed seconds.

    Subtraction in UTC is essential. Wall-clock midnight gives incorrect results
    across daylight-saving changes and for trips continuing from the day before.
    """
    if seconds < 0:
        raise ValueError("GTFS departure seconds cannot be negative")
    noon = dt.datetime.combine(service_date, dt.time(12), CHICAGO).astimezone(UTC)
    return noon - dt.timedelta(hours=12) + dt.timedelta(seconds=seconds)


def event_departures(rows: list[dict[str, Any]], event_date: dt.date,
                     source_retrieved_at: str | None, prepared_at: str) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for row in rows:
        service_date = dt.date.fromisoformat(row["service_date"])
        first = gtfs_time(service_date, int(row["first_departure_seconds"]))
        last = gtfs_time(service_date, int(row["last_departure_seconds"]))
        first_local, last_local = first.astimezone(CHICAGO), last.astimezone(CHICAGO)
        if first_local.date() != event_date:
            continue
        if last_local.date() != event_date:
            raise ValueError("an hourly service aggregate crosses the actual event-date boundary")
        key = (row["service_date"], row["stop_id"], row["route_id"], row["service_hour"])
        if key in seen:
            raise ValueError(f"duplicate source service-hour aggregate: {key}")
        seen.add(key)
        bucket = gtfs_time(service_date, int(row["service_hour"]) * 3600)
        output.append({"event_date": event_date.isoformat(), "service_date": row["service_date"],
                       "stop_id": row["stop_id"], "route_id": row["route_id"], "service_hour": row["service_hour"],
                       "actual_local_date": first_local.date().isoformat(), "actual_local_hour": first_local.hour,
                       "local_fold": first_local.fold, "bucket_start_utc": bucket.isoformat(),
                       "bucket_end_utc": (bucket + dt.timedelta(hours=1)).isoformat(),
                       "first_departure_utc": first.isoformat(), "last_departure_utc": last.isoformat(),
                       "first_departure_local": first_local.isoformat(), "last_departure_local": last_local.isoformat(),
                       "scheduled_departures": row["scheduled_departures"],
                       "first_departure_seconds": row["first_departure_seconds"],
                       "last_departure_seconds": row["last_departure_seconds"],
                       "is_previous_service_date": service_date < event_date, "timezone": "America/Chicago",
                       "data_kind": "static_scheduled_departures", "source": row.get("source") or TRANSIT_URL,
                       "source_retrieved_at": source_retrieved_at, "prepared_at": prepared_at})
    return output


def prepare_transit(payload: bytes, event_date: dt.date, source_retrieved_at: str | None,
                    reference: dt.datetime, prepared_at: str, max_age_hours: float = 168) -> tuple[list[dict], dict]:
    details, output = [], []
    for service_date in (event_date - dt.timedelta(days=1), event_date):
        tables, info = normalize_feed(payload, service_date, source_retrieved_at or prepared_at)
        output.extend(event_departures(tables["transit_departures"], event_date, source_retrieved_at, prepared_at))
        details.append(info)
    previous, current = details
    event_active = bool(current["active_service_ids"])
    previous_active = bool(previous["active_service_ids"])
    if output and event_active:
        status = "scheduled_service_available"
    elif output:
        status = "previous_service_date_only"
    elif current["schedule_start"] and current["schedule_start"] <= event_date.isoformat() <= current["schedule_end"]:
        status = "no_scheduled_departures_for_event"
    else:
        status = "outside_feed_coverage"
    age, fresh = freshness(source_retrieved_at, reference, max_age_hours)
    validity = {"event_date": event_date.isoformat(), "previous_service_date": (event_date - dt.timedelta(days=1)).isoformat(),
                "timezone": "America/Chicago", "event_status": status, "event_service_active": event_active,
                "previous_service_active": previous_active, "schedule_start": current["schedule_start"],
                "schedule_end": current["schedule_end"], "event_departure_rows": len(output),
                "previous_day_spillover_rows": sum(row["is_previous_service_date"] for row in output),
                "scheduled_departures_total": sum(row["scheduled_departures"] for row in output),
                "source_retrieved_at": source_retrieved_at, "reference_time": reference.isoformat(),
                "source_age_hours": age, "freshness_status": fresh, "max_source_age_hours": float(max_age_hours),
                "source_sha256": hashlib.sha256(payload).hexdigest(), "service_date_details_json": json.dumps(details),
                "data_kind": "static_scheduled_service_not_live_arrivals_or_traffic", "prepared_at": prepared_at}
    return output, validity


def window_bounds(event_date: dt.date) -> dict[str, tuple[dt.datetime, dt.datetime]]:
    tomorrow = event_date + dt.timedelta(days=1)
    def local(date: dt.date, hour: int) -> dt.datetime:
        return dt.datetime.combine(date, dt.time(hour), CHICAGO).astimezone(UTC)
    return {"civil_day": (local(event_date, 0), local(tomorrow, 0)),
            "daytime": (local(event_date, 6), local(event_date, 22)),
            "overnight": (local(event_date, 22), local(tomorrow, 6))}


def interval_overlap(row: dict[str, Any], start: dt.datetime, end: dt.datetime) -> bool:
    return aware_time(row["valid_start"]) < end and aware_time(row["valid_end"]) > start


def covered_temperature(rows: list[dict], start: dt.datetime, end: dt.datetime) -> tuple[float, list[float]]:
    intervals, temperatures = [], []
    for row in rows:
        temperature = row.get("temperature_f")
        if temperature is None or not math.isfinite(float(temperature)):
            continue
        left, right = max(start, aware_time(row["valid_start"])), min(end, aware_time(row["valid_end"]))
        if left < right:
            intervals.append((left, right))
            temperatures.append(float(temperature))
    merged: list[tuple[dt.datetime, dt.datetime]] = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(right, merged[-1][1])
        else:
            merged.append((left, right))
    return sum((right - left).total_seconds() / 3600 for left, right in merged), temperatures


def latest_grid_snapshot(rows: list[dict]) -> tuple[list[dict], int, int]:
    """Choose one coherent retrieval/issue/generation/source snapshot for a grid."""
    if not rows:
        return [], 0, 0
    def identity(row: dict) -> tuple[str, str, str, str]:
        retrieved, issued, generated = (aware_time(row[key]).isoformat() if row.get(key) else "" for key in
                                       ("retrieved_at", "forecast_issued_at", "forecast_generated_at"))
        return retrieved, issued, generated, str(row.get("source") or "")
    latest = max(identity(row) for row in rows)
    selected = [row for row in rows if identity(row) == latest]
    unique: dict[str, dict] = {}
    duplicates = 0
    for row in selected:
        start, end = aware_time(row["valid_start"]), aware_time(row["valid_end"])
        if start >= end:
            raise ValueError("weather period must have a positive duration")
        key = start.isoformat()
        if key in unique:
            if json.dumps(unique[key], sort_keys=True) != json.dumps(row, sort_keys=True):
                raise ValueError(f"conflicting duplicate weather period at {key}")
            duplicates += 1
        else:
            unique[key] = row
    return [unique[key] for key in sorted(unique)], duplicates, len(rows) - len(selected)


def prepare_weather(rows: list[dict], event_date: dt.date, reference: dt.datetime,
                    prepared_at: str, max_age_hours: float = 6) -> tuple[list[dict], list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["grid_key"]].append(row)
    if not grouped:
        # Explicit missing-point summaries avoid silently reporting zero requested grids.
        for name, latitude, longitude in POINTS:
            grouped[f"unresolved/{name}"] = []
    bounds = window_bounds(event_date)
    full_start, full_end = bounds["civil_day"][0], bounds["overnight"][1]
    hourly, summaries = [], []
    for grid_key, candidates in sorted(grouped.items()):
        selected, duplicate_count, old_count = latest_grid_snapshot(candidates)
        first = selected[0] if selected else {}
        if not selected:
            name = grid_key.split("/", 1)[1]
            point = next((point for point in POINTS if point[0] == name), (name, None, None))
            first = {"point_names": point[0], "latitude": point[1], "longitude": point[2]}
        age, fresh = freshness(first.get("retrieved_at"), reference, max_age_hours)
        if fresh == "fresh" and any(first.get(key) and aware_time(first[key]) > reference for key in
                                     ("forecast_issued_at", "forecast_generated_at")):
            fresh = "forecast_issued_after_reference"
        event_rows = [row for row in selected if interval_overlap(row, full_start, full_end)]
        for row in event_rows:
            values = {key: row.get(key) for key, _ in HOURLY_SCHEMA}
            local = aware_time(row["valid_start"]).astimezone(CHICAGO)
            values.update(valid_start=aware_time(row["valid_start"]).isoformat(),
                          valid_end=aware_time(row["valid_end"]).isoformat(),
                          local_date=local.date().isoformat(), local_hour=local.hour,
                          event_date=event_date.isoformat(),
                          is_daytime_window=interval_overlap(row, *bounds["daytime"]),
                          is_overnight_window=interval_overlap(row, *bounds["overnight"]),
                          local_fold=local.fold, data_kind="hourly_forecast_not_observation", prepared_at=prepared_at)
            hourly.append(values)
        summary = {"event_date": event_date.isoformat(), "grid_key": grid_key,
                   "point_names": first.get("point_names"), "latitude": first.get("latitude"),
                   "longitude": first.get("longitude"), "timezone": "America/Chicago",
                   "coverage_status": "missing_source_grid" if not selected else "outside_forecast_coverage" if not event_rows else "partial",
                   "source_valid_start": min((aware_time(row["valid_start"]).isoformat() for row in selected), default=None),
                   "source_valid_end": max((aware_time(row["valid_end"]).isoformat() for row in selected), default=None),
                   "forecast_issued_at": first.get("forecast_issued_at"),
                   "forecast_generated_at": first.get("forecast_generated_at"),
                   "source_retrieved_at": first.get("retrieved_at"), "source": first.get("source"),
                   "reference_time": reference.isoformat(), "source_age_hours": age, "freshness_status": fresh,
                   "max_source_age_hours": float(max_age_hours), "hourly_rows_in_event_window": len(event_rows),
                   "duplicate_rows_removed": duplicate_count, "older_snapshot_rows_excluded": old_count,
                   "data_kind": "forecast_not_observation", "prepared_at": prepared_at}
        temperature_sets = {}
        for kind, (start, end) in bounds.items():
            covered, temperatures = covered_temperature(selected, start, end)
            expected = (end - start).total_seconds() / 3600
            complete = math.isclose(covered, expected, abs_tol=1e-8)
            summary.update({f"{kind}_start": start.isoformat(), f"{kind}_end": end.isoformat(),
                            f"{kind}_expected_hours": expected, f"{kind}_covered_hours": covered,
                            f"{kind}_missing_hours": max(0.0, expected - covered), f"{kind}_complete": complete})
            temperature_sets[kind] = temperatures
        civil, day, night = (temperature_sets[kind] for kind in ("civil_day", "daytime", "overnight"))
        # Only complete temperature windows may claim an event high/low.
        summary.update(forecast_high_f=max(civil) if civil and summary["civil_day_complete"] else None,
                       forecast_low_f=min(civil) if civil and summary["civil_day_complete"] else None,
                       available_civil_day_max_f=max(civil) if civil else None,
                       daytime_high_f=max(day) if day and summary["daytime_complete"] else None,
                       available_daytime_max_f=max(day) if day else None,
                       overnight_low_f=min(night) if night and summary["overnight_complete"] else None,
                       available_overnight_min_f=min(night) if night else None)
        if summary["civil_day_complete"] and summary["overnight_complete"]:
            summary["coverage_status"] = "complete"
        summaries.append(summary)
    return hourly, summaries


def validity_summary(event_date: dt.date, weather: list[dict], transit: dict,
                     reference: dt.datetime, prepared_at: str, expected_grids: int = 6) -> dict:
    civil = sum(row["civil_day_complete"] for row in weather)
    day = sum(row["daytime_complete"] for row in weather)
    night = sum(row["overnight_complete"] for row in weather)
    fresh = sum(row["freshness_status"] == "fresh" for row in weather)
    if not any(row["hourly_rows_in_event_window"] for row in weather):
        weather_status = "outside_forecast_coverage_or_missing_source"
    elif civil == len(weather) and night == len(weather):
        weather_status = "complete_event_and_overnight"
    elif night == len(weather):
        weather_status = "partial_day_complete_overnight"
    else:
        weather_status = "partial_event_weather"
    retrievals = [row["source_retrieved_at"] for row in weather if row.get("source_retrieved_at")]
    all_present_fresh = len(weather) == expected_grids and fresh == expected_grids
    return {"event_date": event_date.isoformat(), "timezone": "America/Chicago", "reference_time": reference.isoformat(),
            "weather_status": weather_status,
            "weather_grid_count": sum(row["coverage_status"] != "missing_source_grid" for row in weather),
            "weather_expected_grid_count": expected_grids,
            "weather_fresh_grid_count": fresh, "civil_day_complete_grid_count": civil, "daytime_complete_grid_count": day,
            "overnight_complete_grid_count": night, "can_publish_civil_day_forecast_high": all_present_fresh and civil == expected_grids,
            "can_publish_daytime_high": all_present_fresh and day == expected_grids,
            "can_publish_overnight_low": all_present_fresh and night == expected_grids,
            "weather_retrieved_at_min": min(retrievals, default=None), "weather_retrieved_at_max": max(retrievals, default=None),
            "transit_status": transit["event_status"], "transit_freshness_status": transit["freshness_status"],
            "transit_source_retrieved_at": transit["source_retrieved_at"], "transit_departure_rows": transit["event_departure_rows"],
            "event_readiness_confirmed": False, "daytime_definition": "06:00–22:00 America/Chicago on event date",
            "overnight_definition": "22:00 event date–06:00 following date America/Chicago",
            "limitations_json": json.dumps([
                "NWS values are forecasts from the preserved source snapshot, not observations or heat-campaign temperatures",
                "Partial-window extrema remain explicitly available-only and do not become full-day forecast highs",
                "GTFS data are scheduled service; no live arrivals, disruptions, historical traffic or accessible itinerary is inferred",
                "Both source service dates can contribute to the same actual hour; retain service_date in join keys or aggregate counts",
                "No facility readiness, operator confirmation or dispatch decision is supplied",
            ]), "prepared_at": prepared_at}


def validate_tables(tables: dict[str, list[dict]]) -> None:
    for name, rows in tables.items():
        expected = {key for key, _ in SCHEMAS[name]}
        for row in rows:
            if set(row) != expected:
                raise ValueError(f"{name} fields do not match schema: {set(row) ^ expected}")
            json.dumps(row, allow_nan=False)
    weather_keys = [(row["event_date"], row["grid_key"], row["valid_start"]) for row in tables["event_weather_hourly"]]
    if len(weather_keys) != len(set(weather_keys)):
        raise ValueError("weather event/grid/UTC-period keys must be unique")
    transit_keys = [(row["event_date"], row["service_date"], row["stop_id"], row["route_id"], row["service_hour"])
                    for row in tables["event_transit_departures"]]
    if len(transit_keys) != len(set(transit_keys)):
        raise ValueError("transit source service-date aggregate keys must be unique")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event-date", type=dt.date.fromisoformat, default=dt.date(2026, 11, 17))
    parser.add_argument("--transit-zip", type=Path, default=ROOT / "data/enrichment/transit/google_transit.zip")
    parser.add_argument("--weather-hourly", type=Path, default=ROOT / "data/enrichment/weather/weather_hourly.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/enrichment/event")
    parser.add_argument("--reference-time", type=aware_time, help="UTC/offset timestamp used for source freshness checks")
    parser.add_argument("--max-weather-age-hours", type=float, default=6)
    parser.add_argument("--max-transit-age-hours", type=float, default=168)
    args = parser.parse_args()
    if any(not math.isfinite(age) or age < 0 for age in (args.max_weather_age_hours, args.max_transit_age_hours)):
        parser.error("source age limits must be nonnegative")
    prepared = dt.datetime.now(UTC)
    reference = args.reference_time or prepared
    prepared_at = prepared.isoformat()
    payload = args.transit_zip.read_bytes()
    transit_manifest = args.transit_zip.parent / "manifest.json"
    feed_retrieved_at = None
    if transit_manifest.exists():
        cached = json.loads(transit_manifest.read_text())
        if cached.get("sha256") == hashlib.sha256(payload).hexdigest():
            feed_retrieved_at = cached.get("retrieved_at")
    weather_source = [json.loads(line) for line in args.weather_hourly.read_text().splitlines() if line.strip()]
    departures, transit_status = prepare_transit(payload, args.event_date, feed_retrieved_at, reference,
                                                prepared_at, args.max_transit_age_hours)
    weather_hourly, weather_summary = prepare_weather(weather_source, args.event_date, reference,
                                                      prepared_at, args.max_weather_age_hours)
    event_status = validity_summary(args.event_date, weather_summary, transit_status, reference, prepared_at)
    tables = {"event_transit_departures": departures, "event_transit_validity": [transit_status],
              "event_weather_hourly": weather_hourly, "event_weather_summary": weather_summary,
              "event_data_validity": [event_status]}
    validate_tables(tables)
    run_dir = args.output_dir / args.event_date.isoformat() / prepared.strftime("%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, rows in tables.items():
        digest = hashlib.sha256()
        with (run_dir / f"{name}.jsonl").open("wb") as handle:
            for row in rows:
                raw = (json.dumps(row, allow_nan=False) + "\n").encode()
                digest.update(raw)
                handle.write(raw)
        files[name] = {"rows": len(rows), "sha256": digest.hexdigest()}
    manifest = {"event_date": args.event_date.isoformat(), "prepared_at": prepared_at, "reference_time": reference.isoformat(),
                "source_files": {"transit_zip": str(args.transit_zip), "weather_hourly": str(args.weather_hourly)},
                "weather_source_sha256": hashlib.sha256(args.weather_hourly.read_bytes()).hexdigest(),
                "transit_source_sha256": hashlib.sha256(payload).hexdigest(), "files": files,
                "validity": event_status, "cloud_writes": 0, "network_requests": 0}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output_dir": str(run_dir), "files": files, "validity": event_status}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
