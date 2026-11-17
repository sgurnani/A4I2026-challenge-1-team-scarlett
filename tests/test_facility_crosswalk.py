import json

from reconcile_facilities import reconcile

NOW = "2026-11-17T00:00:00Z"


def school(school_id, name=None, *, latitude=41.88, address="1 N State St"):
    return {"school_id": school_id, "short_name": name or f"School {school_id}",
            "full_name": name or f"School {school_id}", "address": address,
            "latitude": latitude, "longitude": -87.63, "grade_cat": "HS",
            "facility_type": "High School", "has_ac_gym": True,
            "source": "data.cityofchicago.org/c7jj-qjvh", "retrieved_at": NOW}


def test_preserves_all_187_legacy_schools_including_35_without_current_match():
    current = [school(str(index), address=f"{index + 1} N State St") for index in range(170)]
    legacy = [school(str(index), address=f"{index + 1} N State St") for index in range(152)]
    legacy += [school(str(index), address=f"{index + 1} N State St") for index in range(200, 233)]
    legacy += [school("", "Missing ID A", address="901 N State St"),
               school("", "Missing ID B", address="902 N State St")]
    sources = {"chi_site_cps_schools": current, "high_schools": legacy}
    tables, report = reconcile(sources, NOW)
    links = [row for row in tables["facility_source_crosswalk"] if row["source_table"] == "high_schools"]
    assert len(links) == len({row["source_record_id"] for row in links}) == 187
    assert len({row["facility_id"] for row in links}) == 187
    assert report["legacy_high_schools_without_current_cps_match"] == 35
    assert report["legacy_high_schools_matched_current_cps"] == 152
    assert report["identity_review_required_by_source"]["high_schools"] == 35


def test_different_school_ids_are_not_collapsed_on_shared_campus():
    sources = {"chi_site_cps_schools": [school("100", "Shared school"), school("200", "Shared school")],
               "high_schools": [school("100", "Shared school"), school("200", "Shared school")]}
    tables, _ = reconcile(sources, NOW)
    assert {row["facility_id"] for row in tables["canonical_facilities"]} == {"cps-school-100", "cps-school-200"}
    assert len(tables["facility_source_crosswalk"]) == 4


def test_authoritative_current_school_coordinates_win_and_claims_remain_unverified():
    current = school("100", latitude=41.88)
    legacy = school("100", latitude=41.68)
    tables, report = reconcile({"chi_site_cps_schools": [current], "high_schools": [legacy]}, NOW)
    facility = tables["canonical_facilities"][0]
    assert facility["latitude"] == 41.88
    assert facility["coordinate_source_table"] == "chi_site_cps_schools"
    assert facility["has_source_coordinate_conflict"] is True
    assert report["coordinate_conflict_by_source"]["high_schools"] == 1
    links = [row for row in tables["facility_source_crosswalk"] if row["source_table"] == "high_schools"]
    assert json.loads(links[0]["source_claims_json"])["has_ac_gym"] is True
    readiness = tables["facility_readiness"][0]
    assert readiness["working_ac_confirmed"] is None
    assert readiness["readiness_status"] == "unconfirmed"


def test_106_coordinate_disagreements_remain_auditable_without_ready_schools():
    current = [school(str(index), latitude=41.88) for index in range(106)]
    legacy = [school(str(index), latitude=41.68) for index in range(106)]
    tables, report = reconcile({"chi_site_cps_schools": current, "high_schools": legacy}, NOW)
    assert report["coordinate_conflict_by_source"]["high_schools"] == 106
    assert report["coordinate_conflict_canonical_facilities"] == 106
    assert not any(row["is_indoor_inventory_access_candidate"] for row in tables["canonical_facilities"])
    assert all(row["readiness_status"] == "unconfirmed" for row in tables["facility_readiness"])


def test_all_cps_school_membership_does_not_imply_high_school_candidate_eligibility():
    elementary = school("100", "Elementary school")
    elementary["grade_cat"] = "ES"
    high = school("200", "High school")
    candidate = {"site_key": "cps-200", "site_category": "CPS High School (candidate)",
                 "site_name": "High school", "address": high["address"],
                 "latitude": high["latitude"], "longitude": high["longitude"]}
    tables, report = reconcile({"chi_site_cps_schools": [elementary, high],
                               "chi_site_candidate_sites": [candidate]}, NOW)
    assert report["current_cps_school_facilities"] == 2
    assert report["current_cps_non_high_school_facilities"] == 1
    assert report["current_high_school_candidate_facilities"] == 1
    assert not any(row["is_indoor_inventory_access_candidate"] for row in tables["canonical_facilities"])


def test_43_parks_preserve_conflicting_source_claims_without_inventing_indoor_readiness():
    snapshot = [{"facility_type": "Park District Facility", "site_name": f"Park {index}",
                 "address": f"{index + 1} W Park Ave", "latitude": 41.88, "longitude": -87.63,
                 "is_indoor": False, "source": "data.cityofchicago.org/msrk-w9ih"} for index in range(43)]
    candidates = [{**row, "site_category": row["facility_type"], "indoor_air_conditioned": True,
                   "site_key": f"cool-{index}"} for index, row in enumerate(snapshot)]
    tables, report = reconcile({"cooling_inventory_snapshot": snapshot, "chi_site_candidate_sites": candidates}, NOW)
    assert len(tables["canonical_facilities"]) == 43
    assert report["park_facility_source_rows"] == 43
    assert report["park_facilities_readiness_unconfirmed"] == 43
    assert all(row["physical_setting"] == "unknown" for row in tables["canonical_facilities"])
    assert all(row["indoor_cooling_classification"] == "unconfirmed" for row in tables["canonical_facilities"])
    assert all(row["working_ac_confirmed"] is None for row in tables["facility_readiness"])
    assert report["unverified_operational_claims_by_source"]["chi_site_candidate_sites"] == 43


def test_ids_remain_stable_when_source_rows_reorder_and_invalid_points_are_retained():
    rows = [school("", "Missing ID A", address="901 N State St"),
            school("", "Missing ID B", address="902 N State St")]
    rows[0]["latitude"] = "NaN"
    first, _ = reconcile({"high_schools": rows}, NOW)
    second, _ = reconcile({"high_schools": list(reversed(rows))}, NOW)
    assert first == second
    assert len(first["canonical_facilities"]) == 2
    assert len(first["facility_source_crosswalk"]) == 2
    assert any(row["latitude"] is None for row in first["canonical_facilities"])


def test_ambiguous_address_matches_remain_separate_for_review():
    libraries = [{"branch_": name, "address": "1 N State St", "latitude": 41.88, "longitude": -87.63,
                  "website": f"https://www.chipublib.org/locations/{index}/"}
                 for index, name in enumerate(["First", "Second"], 1)]
    snapshot = [{"facility_type": "Library", "site_name": "Other name", "address": "1 N State St",
                 "latitude": 41.88, "longitude": -87.63}]
    tables, report = reconcile({"chi_site_libraries": libraries, "cooling_inventory_snapshot": snapshot}, NOW)
    assert len(tables["canonical_facilities"]) == 3
    assert report["identity_review_required_by_source"]["cooling_inventory_snapshot"] == 1
    link = [row for row in tables["facility_source_crosswalk"] if row["source_table"] == "cooling_inventory_snapshot"][0]
    assert link["match_method"] == "ambiguous_colocated_facilities"
