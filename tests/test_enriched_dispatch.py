"""Prevent inventory assumptions becoming operational facts in dispatch sheets."""
from heat_dispatch import dispatch


def test_high_school_candidate_does_not_invent_hours_or_working_ac():
    sites = [{"nearest_high_school_name": "Jones College Prep", "nearest_high_school_address": "700 S State",
              "km_to_high_school": 1.2, "nearest_cooling_type": "Library"}]
    schools = dispatch.pick_high_schools(sites, 1)
    assert len(schools) == 1
    assert schools[0]["current_hours"] is None
    sheet = dispatch.render_sheet(100, [], [], [], [], {}, schools)
    assert "Large-capacity AC" not in sheet
    assert "7AM-7PM" not in sheet
    assert "unconfirmed" in sheet and "Listed hours: unknown" in sheet


def test_enriched_cooling_readiness_is_preserved_from_stored_rows():
    site = {"latitude": 41.8, "longitude": -87.6, "predicted_offset_f": 2,
            "nearest_cooling_name": "Library", "nearest_cooling_status": "listed_unconfirmed",
            "nearest_cooling_event_verified": False}
    stop = dispatch.pick_hydration_stops([site], 100, 1)[0]
    assert stop["cooling_readiness_status"] == "listed_unconfirmed"
    assert stop["cooling_event_verified"] is False
    assert stop["expected_air_temp_f"] == 102
