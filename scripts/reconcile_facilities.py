#!/usr/bin/env python3
"""Reconcile facility inventories locally, preserving source rows and unknown readiness.

Read existing BigQuery inventories with --fetch --project PROJECT, or recompute
from --source-dir snapshots. This script has no cloud write path.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ORDER = (
    "chi_site_cps_schools", "chi_site_libraries", "chi_site_police_stations",
    "chi_site_senior_centers", "cooling_inventory_snapshot", "high_schools",
    "chi_site_cooling_centers", "chi_site_candidate_sites", "cooling_facilities",
)
COORDINATE_CONFLICT_M = 250.0
ADDRESS_MATCH_MAX_M = 250.0
SCHEMAS = {
    "canonical_facilities": [
        ("facility_id", "STRING"), ("facility_name", "STRING"), ("facility_category", "STRING"),
        ("address", "STRING"), ("zip", "STRING"), ("latitude", "FLOAT64"), ("longitude", "FLOAT64"),
        ("coordinate_source_table", "STRING"), ("coordinate_source_record_id", "STRING"),
        ("school_id", "STRING"), ("is_high_school", "BOOL"), ("is_legacy_high_school", "BOOL"),
        ("is_current_cps_high_school", "BOOL"), ("is_current_cps_school", "BOOL"),
        ("is_current_high_school_candidate", "BOOL"), ("in_cooling_inventory", "BOOL"),
        ("is_indoor_inventory_access_candidate", "BOOL"), ("identity_review_required", "BOOL"),
        ("candidate_status", "STRING"),
        ("physical_setting", "STRING"), ("indoor_cooling_classification", "STRING"),
        ("classification_basis", "STRING"), ("has_source_coordinate_conflict", "BOOL"),
        ("has_source_address_conflict", "BOOL"), ("has_unverified_operational_claims", "BOOL"),
        ("source_record_count", "INT64"), ("source_tables_json", "STRING"), ("retrieved_at", "TIMESTAMP"),
    ],
    "facility_source_crosswalk": [
        ("facility_id", "STRING"), ("source_table", "STRING"), ("source_dataset_id", "STRING"),
        ("source_record_id", "STRING"), ("source_key", "STRING"), ("source_name", "STRING"),
        ("source_address", "STRING"), ("source_latitude", "FLOAT64"), ("source_longitude", "FLOAT64"),
        ("match_method", "STRING"), ("identity_review_required", "BOOL"),
        ("distance_to_canonical_m", "FLOAT64"), ("coordinate_conflict", "BOOL"),
        ("address_conflict", "BOOL"), ("operational_claims_unverified", "BOOL"),
        ("source_claims_json", "STRING"), ("source_row_json", "STRING"),
        ("source_retrieved_at", "TIMESTAMP"), ("retrieved_at", "TIMESTAMP"),
    ],
    "facility_readiness": [
        ("facility_id", "STRING"), ("event_date", "DATE"), ("readiness_status", "STRING"),
        ("open_for_event", "BOOL"), ("available_capacity", "INT64"), ("working_ac_confirmed", "BOOL"),
        ("staffing_confirmed", "BOOL"), ("wheelchair_accessible_confirmed", "BOOL"),
        ("verified_by", "STRING"), ("readiness_verified_at", "TIMESTAMP"),
        ("verification_source", "STRING"), ("retrieved_at", "TIMESTAMP"),
    ],
}


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def normalize_address(value: Any) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split()
    aliases = {"north": "n", "south": "s", "east": "e", "west": "w", "street": "st",
               "avenue": "ave", "av": "ave", "boulevard": "blvd", "road": "rd",
               "drive": "dr", "place": "pl", "parkway": "pkwy", "court": "ct"}
    return " ".join(aliases.get(word, word) for word in words)


def stable_hash(*parts: Any) -> str:
    value = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def finite_coordinate(value: Any, low: float, high: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def distance_m(first: dict[str, Any], second: dict[str, Any]) -> float | None:
    values = [first.get("latitude"), first.get("longitude"), second.get("latitude"), second.get("longitude")]
    if any(value is None for value in values):
        return None
    lat1, lon1, lat2, lon2 = (math.radians(float(value)) for value in values if value is not None)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371008.8 * 2 * math.asin(min(1.0, math.sqrt(h)))


def school_id_for(table: str, row: dict[str, Any]) -> str | None:
    direct = str(row.get("school_id") or "").strip()
    if direct:
        return direct
    if table == "chi_site_candidate_sites":
        key = str(row.get("site_key") or "")
        if key.startswith("cps-") and key[4:]:
            return key[4:]
    return None


def category_for(table: str, row: dict[str, Any]) -> str:
    if table == "chi_site_cps_schools":
        return "high_school" if row.get("grade_cat") == "HS" else "school"
    if table == "high_schools":
        return "high_school"
    if table == "chi_site_libraries":
        return "library"
    if table == "chi_site_police_stations":
        return "police_station"
    if table == "chi_site_senior_centers":
        return "senior_center"
    kind = str(row.get("facility_type") or row.get("site_type") or row.get("site_category") or "").lower()
    if "school" in kind:
        return "high_school"
    if "spray" in kind:
        return "spray_feature"
    if "park district" in kind:
        return "park_facility"
    if "library" in kind:
        return "library"
    if "police" in kind:
        return "police_station"
    if "senior" in kind:
        return "senior_center"
    if "college" in kind:
        return "college"
    if "community service" in kind:
        return "community_service_center"
    return "other"


def source_name_for(table: str, row: dict[str, Any]) -> str:
    if table == "chi_site_police_stations":
        return str(row.get("district_name") or row.get("district") or "Police station")
    return str(row.get("site_name") or row.get("full_name") or row.get("school_name") or
               row.get("short_name") or row.get("branch_") or "Unnamed facility")


def source_dataset_for(row: dict[str, Any]) -> str | None:
    value = row.get("source_dataset_id") or row.get("source")
    if value:
        match = re.search(r"[a-z0-9]{4}-[a-z0-9]{4}", str(value))
        return match.group(0) if match else str(value)
    return None


def source_records(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    # Deterministic ordering preserves fallback IDs independently of input ordering.
    ordered = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))
    occurrences: Counter[str] = Counter()
    for row in ordered:
        name = source_name_for(table, row)
        address = str(row.get("address") or "")
        category = category_for(table, row)
        school_id = school_id_for(table, row)
        source_key = str(row.get("site_key") or school_id or row.get("district") or "") or None
        identity = source_key or stable_hash(category, normalize_text(name), normalize_address(address),
                                             row.get("program") if table == "chi_site_senior_centers" else None)
        occurrences[identity] += 1
        record_id = f"{table}:{identity}"
        if occurrences[identity] > 1:
            record_id += f":duplicate-{occurrences[identity]}"
        claims = {key: row[key] for key in (
            "is_indoor", "has_ac_gym", "indoor_air_conditioned", "open_24_7", "is_current_cooling_site",
            "hours_of_operation", "service_hours", "is_high_school", "grade_cat", "open_for_event",
            "working_ac_confirmed", "staffing_confirmed", "available_capacity",
        ) if key in row}
        records.append({"source_table": table, "source_dataset_id": source_dataset_for(row),
                        "source_record_id": record_id, "source_key": source_key, "name": name,
                        "address": address, "zip": str(row.get("zip") or "") or None,
                        "category": category, "school_id": school_id,
                        "latitude": finite_coordinate(row.get("latitude"), -90, 90),
                        "longitude": finite_coordinate(row.get("longitude"), -180, 180),
                        "claims": claims, "raw": row})
    return records


def preferred_id(record: dict[str, Any]) -> str:
    if record["school_id"]:
        return f"cps-school-{record['school_id']}"
    row, table = record["raw"], record["source_table"]
    if table == "chi_site_libraries":
        location = re.search(r"/locations/(\d+)", str(row.get("website") or ""))
        if location:
            return f"chicago-library-{location.group(1)}"
    if table == "chi_site_police_stations" and row.get("district"):
        return f"chicago-police-{row['district']}"
    return f"chicago-{record['category']}-{stable_hash(record['category'], normalize_text(record['name']), normalize_address(record['address']))}"


def compatible(record: dict[str, Any], entity: dict[str, Any]) -> bool:
    if record["school_id"] and entity.get("school_id") and record["school_id"] != entity["school_id"]:
        return False
    groups = {"high_school", "school"}
    return record["category"] == entity["category"] or {record["category"], entity["category"]} <= groups


def match_record(record: dict[str, Any], entities: dict[str, dict[str, Any]]) -> tuple[str | None, str, bool]:
    preferred = preferred_id(record)
    if record["school_id"]:
        # Never merge different official school IDs merely because schools share a campus.
        if preferred in entities:
            return preferred, "official_school_id", False
        if record["source_table"] == "high_schools":
            return None, "legacy_school_id_without_current_cps_match", True
        return None, "new_official_school_id", False
    if preferred in entities:
        return preferred, "stable_source_identity", False
    if record["source_table"] in {"chi_site_libraries", "chi_site_police_stations", "chi_site_senior_centers"}:
        # Distinct authoritative inventory identities can share a building.
        return None, "primary_inventory_identity", False
    address, name = normalize_address(record["address"]), normalize_text(record["name"])
    exact = [key for key, entity in entities.items() if compatible(record, entity) and address and
             any(normalize_address(alias["address"]) == address and normalize_text(alias["name"]) == name
                 for alias in entity["records"])]
    if len(exact) == 1:
        return exact[0], "exact_name_and_address", False
    if len(exact) > 1:
        return None, "ambiguous_exact_identity", True
    nearby = [key for key, entity in entities.items() if compatible(record, entity) and address and
              any(normalize_address(alias["address"]) == address for alias in entity["records"]) and
              (gap := distance_m(record, entity)) is not None and gap <= ADDRESS_MATCH_MAX_M]
    if len(nearby) == 1:
        return nearby[0], "unique_address_within_250m", False
    if len(nearby) > 1:
        return None, "ambiguous_colocated_facilities", True
    if record["source_table"] in {"chi_site_libraries", "chi_site_police_stations", "chi_site_senior_centers", "cooling_inventory_snapshot"}:
        return None, "primary_inventory_identity", False
    return None, "unmatched_source_identity", True


def physical_classification(entity: dict[str, Any]) -> tuple[str, str, str]:
    category = entity["category"]
    if category == "spray_feature":
        return "outdoor", "outdoor_only", "Published facility type is Park District Spray Feature"
    if category == "park_facility":
        return "unknown", "unconfirmed", "Park District Facility label does not establish an indoor, working-air-conditioned space"
    if category in {"high_school", "school"}:
        return "indoor_building_candidate", "unconfirmed", "School inventory establishes a school, not public cooling access, AC, staffing or opening"
    if entity["in_cooling_inventory"]:
        return "indoor_inventory_listing", "listed_unconfirmed", "Chicago cooling inventory category lists an indoor site; event readiness has not been verified"
    return "indoor_building_candidate", "unconfirmed", "Facility inventory is not a verified heat-event operating roster"


def reconcile(sources: dict[str, list[dict[str, Any]]], retrieved_at: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    unknown_sources = set(sources) - set(SOURCE_ORDER)
    if unknown_sources:
        raise ValueError(f"unsupported source tables: {sorted(unknown_sources)}")
    entities: dict[str, dict[str, Any]] = {}
    assignments: list[tuple[dict[str, Any], str, str, bool]] = []
    for table in SOURCE_ORDER:
        for record in source_records(table, sources.get(table, [])):
            facility_id, method, review = match_record(record, entities)
            if facility_id is None:
                facility_id = preferred_id(record)
                # Preserve ambiguous records instead of overwriting an existing entity.
                if facility_id in entities:
                    facility_id += "-" + stable_hash(record["source_record_id"])
                entities[facility_id] = {"name": record["name"], "category": record["category"],
                                         "address": record["address"], "zip": record["zip"],
                                         "latitude": record["latitude"], "longitude": record["longitude"],
                                         "coordinate_source_table": table,
                                         "coordinate_source_record_id": record["source_record_id"],
                                         "school_id": record["school_id"], "records": [],
                                         "in_cooling_inventory": False}
            entity = entities[facility_id]
            entity["records"].append(record)
            if table in {"cooling_inventory_snapshot", "chi_site_cooling_centers"}:
                entity["in_cooling_inventory"] = True
            if entity["latitude"] is None and record["latitude"] is not None and record["longitude"] is not None:
                entity.update(latitude=record["latitude"], longitude=record["longitude"],
                              coordinate_source_table=table, coordinate_source_record_id=record["source_record_id"])
            assignments.append((record, facility_id, method, review))
    crosswalk = []
    for record, facility_id, method, review in assignments:
        entity = entities[facility_id]
        gap = distance_m(record, entity)
        claimed = any(record["claims"].get(key) is True for key in (
            "has_ac_gym", "indoor_air_conditioned", "open_24_7", "open_for_event", "working_ac_confirmed", "staffing_confirmed"))
        crosswalk.append({"facility_id": facility_id, "source_table": record["source_table"],
                          "source_dataset_id": record["source_dataset_id"], "source_record_id": record["source_record_id"],
                          "source_key": record["source_key"], "source_name": record["name"],
                          "source_address": record["address"], "source_latitude": record["latitude"],
                          "source_longitude": record["longitude"], "match_method": method,
                          "identity_review_required": review, "distance_to_canonical_m": gap,
                          "coordinate_conflict": gap is not None and gap > COORDINATE_CONFLICT_M,
                          "address_conflict": bool(record["address"] and entity["address"] and
                                                   normalize_address(record["address"]) != normalize_address(entity["address"])),
                          "operational_claims_unverified": claimed,
                          "source_claims_json": json.dumps(record["claims"], sort_keys=True, default=str),
                          "source_row_json": json.dumps(record["raw"], sort_keys=True, default=str),
                          "source_retrieved_at": record["raw"].get("retrieved_at"), "retrieved_at": retrieved_at})
    by_facility: dict[str, list[dict[str, Any]]] = {}
    for row in crosswalk:
        by_facility.setdefault(row["facility_id"], []).append(row)
    canonical = []
    readiness = []
    for facility_id, entity in sorted(entities.items()):
        links = by_facility[facility_id]
        records = entity["records"]
        current = [record for record in records if record["source_table"] == "chi_site_cps_schools"]
        legacy_high = any(record["source_table"] == "high_schools" for record in records)
        current_high = any(record["category"] == "high_school" for record in current)
        hs_candidate = any(record["source_table"] == "chi_site_candidate_sites" and
                           str(record["source_key"] or "").startswith("cps-") for record in records)
        physical, classification, basis = physical_classification(entity)
        indoor_access = entity["in_cooling_inventory"] and classification == "listed_unconfirmed"
        if indoor_access:
            candidate_status = "designated_indoor_inventory_unverified"
        elif hs_candidate:
            candidate_status = "current_high_school_candidate_unverified"
        elif legacy_high and not current:
            candidate_status = "legacy_high_school_identity_unresolved"
        elif current:
            candidate_status = "cps_school_without_high_school_candidate_membership"
        elif classification == "outdoor_only":
            candidate_status = "outdoor_inventory_only"
        else:
            candidate_status = "other_inventory_candidate_unverified"
        canonical.append({"facility_id": facility_id, "facility_name": entity["name"],
                          "facility_category": entity["category"], "address": entity["address"], "zip": entity["zip"],
                          "latitude": entity["latitude"], "longitude": entity["longitude"],
                          "coordinate_source_table": entity["coordinate_source_table"],
                          "coordinate_source_record_id": entity["coordinate_source_record_id"],
                          "school_id": entity["school_id"], "is_high_school": current_high if current else legacy_high,
                          "is_legacy_high_school": legacy_high, "is_current_cps_high_school": current_high,
                          "is_current_cps_school": bool(current), "is_current_high_school_candidate": hs_candidate,
                          "is_indoor_inventory_access_candidate": indoor_access,
                          "identity_review_required": any(row["identity_review_required"] for row in links),
                          "candidate_status": candidate_status,
                          "in_cooling_inventory": entity["in_cooling_inventory"], "physical_setting": physical,
                          "indoor_cooling_classification": classification, "classification_basis": basis,
                          "has_source_coordinate_conflict": any(row["coordinate_conflict"] for row in links),
                          "has_source_address_conflict": any(row["address_conflict"] for row in links),
                          "has_unverified_operational_claims": any(row["operational_claims_unverified"] for row in links),
                          "source_record_count": len(links),
                          "source_tables_json": json.dumps(sorted({row["source_table"] for row in links})),
                          "retrieved_at": retrieved_at})
        readiness.append({"facility_id": facility_id, "event_date": None, "readiness_status": "unconfirmed",
                          "open_for_event": None, "available_capacity": None, "working_ac_confirmed": None,
                          "staffing_confirmed": None, "wheelchair_accessible_confirmed": None, "verified_by": None,
                          "readiness_verified_at": None, "verification_source": None, "retrieved_at": retrieved_at})
    legacy = [row for row in crosswalk if row["source_table"] == "high_schools"]
    candidate = [row for row in crosswalk if row["source_table"] == "chi_site_candidate_sites" and
                 str(row["source_key"] or "").startswith("cps-")]
    current_ids = {row["facility_id"] for row in crosswalk if row["source_table"] == "chi_site_cps_schools"}
    candidate_ids = {row["facility_id"] for row in candidate}
    unmatched = [row for row in legacy if row["facility_id"] not in current_ids]
    parks = [row for row in crosswalk if row["source_table"] == "cooling_inventory_snapshot" and
             json.loads(row["source_row_json"]).get("facility_type") == "Park District Facility"]
    report = {"retrieved_at": retrieved_at, "source_rows": {table: len(sources.get(table, [])) for table in SOURCE_ORDER},
              "source_rows_total": sum(len(rows) for rows in sources.values()), "crosswalk_rows": len(crosswalk),
              "canonical_facilities": len(canonical), "readiness_unconfirmed": len(readiness),
              "legacy_high_school_rows_preserved": len(legacy),
              "legacy_high_school_facility_ids": len({row["facility_id"] for row in legacy}),
              "legacy_high_schools_matched_current_cps": len(legacy) - len(unmatched),
              "legacy_high_schools_matched_current_high_candidates": sum(row["facility_id"] in candidate_ids for row in legacy),
              "legacy_high_schools_without_current_cps_match": len(unmatched),
              "legacy_high_schools_without_current_cps_match_details": [
                  {key: row[key] for key in ("facility_id", "source_record_id", "source_name", "source_address", "match_method")}
                  for row in unmatched],
              "current_high_school_candidates_preserved": len(candidate),
              "current_high_school_candidates_without_legacy_row": len(candidate_ids - {row["facility_id"] for row in legacy}),
              "park_facility_source_rows": len(parks), "park_facility_canonical_ids": len({row["facility_id"] for row in parks}),
              "park_facilities_readiness_unconfirmed": len({row["facility_id"] for row in parks}),
              "identity_review_required_by_source": dict(Counter(row["source_table"] for row in crosswalk if row["identity_review_required"])),
              "identity_review_required_details": [
                  {key: row[key] for key in ("facility_id", "source_table", "source_record_id", "source_name", "source_address", "match_method")}
                  for row in crosswalk if row["identity_review_required"]],
              "coordinate_conflict_source_rows": sum(row["coordinate_conflict"] for row in crosswalk),
              "coordinate_conflict_canonical_facilities": sum(row["has_source_coordinate_conflict"] for row in canonical),
              "coordinate_conflict_by_source": dict(Counter(row["source_table"] for row in crosswalk if row["coordinate_conflict"])),
              "address_conflict_source_rows": sum(row["address_conflict"] for row in crosswalk),
              "unverified_operational_claims_by_source": dict(Counter(row["source_table"] for row in crosswalk if row["operational_claims_unverified"])),
              "classification_counts": dict(Counter(row["indoor_cooling_classification"] for row in canonical)),
              "canonical_facilities_by_category": dict(Counter(row["facility_category"] for row in canonical)),
              "current_cps_school_facilities": sum(row["is_current_cps_school"] for row in canonical),
              "current_cps_non_high_school_facilities": sum(row["is_current_cps_school"] and not row["is_current_cps_high_school"] for row in canonical),
              "current_high_school_candidate_facilities": sum(row["is_current_high_school_candidate"] for row in canonical),
              "indoor_inventory_access_candidates": sum(row["is_indoor_inventory_access_candidate"] for row in canonical),
              "legacy_high_school_current_grade_disagreements": sum(row["is_legacy_high_school"] and row["is_current_cps_school"] and not row["is_current_cps_high_school"] for row in canonical),
              "limitations": ["No operator verification was obtained; all readiness fields are null",
                              "Legacy school membership is preserved, not proof of current school operation",
                              "Official school IDs win over name/address proximity; distinct IDs remain distinct even when colocated",
                              "Matching addresses within 250m only merges compatible types when exactly one candidate exists",
                              "Name/address fallback IDs can change if both identity fields change; keep the crosswalk for migrations",
                              "No fuzzy matching, live opening inference, or cloud writes are performed"]}
    tables = {"canonical_facilities": canonical, "facility_source_crosswalk": sorted(crosswalk, key=lambda row: row["source_record_id"]),
              "facility_readiness": readiness}
    validate_reconciliation(tables, sources)
    return tables, report


def validate_reconciliation(tables: dict[str, list[dict[str, Any]]], sources: dict[str, list[dict[str, Any]]]) -> None:
    canonical = tables["canonical_facilities"]
    ids = {row["facility_id"] for row in canonical}
    if len(ids) != len(canonical):
        raise ValueError("canonical facility IDs are not unique")
    crosswalk = tables["facility_source_crosswalk"]
    keys = {row["source_record_id"] for row in crosswalk}
    if len(keys) != len(crosswalk):
        raise ValueError("crosswalk source IDs are not unique")
    for table in SOURCE_ORDER:
        if sum(row["source_table"] == table for row in crosswalk) != len(sources.get(table, [])):
            raise ValueError(f"source records were dropped: {table}")
    if any(row["facility_id"] not in ids for row in crosswalk + tables["facility_readiness"]):
        raise ValueError("crosswalk/readiness references a nonexistent facility")
    if {row["facility_id"] for row in tables["facility_readiness"]} != ids:
        raise ValueError("every facility must have an explicit unconfirmed readiness record")
    for name, rows in tables.items():
        expected = {field for field, _ in SCHEMAS[name]}
        for row in rows:
            if set(row) != expected:
                raise ValueError(f"fields do not match explicit schema: {name}")
            json.dumps(row, allow_nan=False)


def fetch_sources(project: str, dataset: str, location: str, output_dir: Path) -> dict[str, list[dict[str, Any]]]:
    from google.cloud.bigquery.client import Client
    from load_enrichment_snapshots import gcloud_credentials

    client = Client(project=project, location=location, credentials=gcloud_credentials())
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = {}
    manifest: dict[str, Any] = {"project": project, "dataset": dataset,
                               "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "tables": {}}
    for name in SOURCE_ORDER:
        table = client.get_table(f"{project}.{dataset}.{name}")
        rows = [dict(row.items()) for row in client.query(f"SELECT * FROM `{project}.{dataset}.{name}`").result()]
        serialized = json.dumps(rows, sort_keys=True, default=str)
        (output_dir / f"{name}.json").write_text(serialized + "\n")
        sources[name] = json.loads(serialized)
        manifest["tables"][name] = {"rows": len(rows), "modified": table.modified.isoformat() if table.modified else None,
                                    "schema": [(field.name, field.field_type, field.mode) for field in table.schema],
                                    "sha256": hashlib.sha256(serialized.encode()).hexdigest()}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", type=Path, default=ROOT / ".data/reconciled_facility_sources")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/enrichment/reconciled_facilities")
    parser.add_argument("--fetch", action="store_true", help="Read existing BigQuery source tables using active gcloud credentials")
    parser.add_argument("--project")
    parser.add_argument("--dataset", default="a4i_climate")
    parser.add_argument("--location", default="US")
    args = parser.parse_args()
    if args.fetch:
        if not args.project or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", args.project):
            parser.error("--fetch requires a valid --project")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,1023}", args.dataset):
            parser.error("provide a valid --dataset")
        sources = fetch_sources(args.project, args.dataset, args.location, args.source_dir)
    else:
        sources = {name: json.loads((args.source_dir / f"{name}.json").read_text()) for name in SOURCE_ORDER}
    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
    tables, report = reconcile(sources, retrieved_at)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (args.output_dir / f"{name}.jsonl").open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row, allow_nan=False, sort_keys=True) + "\n")
    report["source_manifest"] = str(args.source_dir / "manifest.json")
    report["cloud_writes"] = 0
    (args.output_dir / "reconciliation_report.json").write_text(json.dumps(report, indent=2) + "\n")
    summary = {key: value for key, value in report.items() if key not in {"legacy_high_schools_without_current_cps_match_details", "identity_review_required_details"}}
    print(json.dumps({"output_dir": str(args.output_dir), **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
