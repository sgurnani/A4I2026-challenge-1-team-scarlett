"""Unit tests for the 311 Heat Desk hotline assistant."""
from __future__ import annotations

from social_media.call_311 import (
    check_heat_illness_urgency,
    find_cooling_for_caller,
    get_311_service_requests,
    intake_wellness_check,
)


def test_emergency_triage_detects_heat_stroke():
    # Emergency cases
    case1 = check_heat_illness_urgency("My grandfather passed out in the living room!")
    assert case1["is_emergency"] is True
    assert case1["escalation"] == "CALL_911_IMMEDIATELY"
    assert "911" in case1["message"]

    case2 = check_heat_illness_urgency("She is confused and stopped sweating in this heat.")
    assert case2["is_emergency"] is True

    # Non-emergency routine cases
    case3 = check_heat_illness_urgency("Looking for an air-conditioned room to study this afternoon.")
    assert case3["is_emergency"] is False


def test_find_cooling_for_caller_in_chicago():
    res = find_cooling_for_caller("63rd and Halsted", limit=3)
    assert res["status"] == "ok"
    assert len(res["nearest_facilities"]) == 3
    closest = res["nearest_facilities"][0]
    # Kennedy-King College is located right at 740 W 63rd St!
    assert "Kennedy-King" in closest["site_name"] or "Sherman" in closest["site_name"]
    assert closest["distance_miles"] < 2.0
    assert "hours" in closest and closest["hours"]
    assert "address" in closest and closest["address"]


def test_find_cooling_for_caller_outside_chicago():
    res = find_cooling_for_caller("Naperville, IL")
    assert res["status"] in ("outside_chicago", "not_found")


def test_intake_wellness_check_success():
    res = intake_wellness_check(
        requester_name="Maria Gomez",
        requester_phone="312-555-0199",
        recipient_name="Rosa Gomez",
        street_address="1140 W 79th Street",
        unit="4B",
        vulnerabilities="Age 82, no working air conditioning, mobility issues",
        notes="Please knock loudly",
    )
    assert res["status"] == "created"
    assert res["sr_number"].startswith("SR-311-HEAT-")
    assert "1140 W 79th Street" in res["address"]

    # Verify retrieval
    requests = get_311_service_requests()
    assert any(r["sr_number"] == res["sr_number"] for r in requests)


def test_intake_wellness_check_escalates_heat_stroke():
    res = intake_wellness_check(
        requester_name="John Doe",
        requester_phone="312-555-0100",
        recipient_name="Jane Doe",
        street_address="2300 S Western Ave",
        vulnerabilities="Passed out from heat and not responding",
    )
    assert res["status"] == "escalated_to_911"
    assert res["ticket"] is None
    assert "911" in res["message"]


def test_log_311_heat_call_and_vulnerability_identification():
    from social_media.call_311 import log_311_heat_call
    from social_media.vulnerability import identify_top_vulnerabilities

    # Log 311 calls specifically reporting lack of vehicle and transit hardship
    res1 = log_311_heat_call(
        location="Austin",
        vulnerabilities="No vehicle / cannot travel to cooling center in extreme heat",
    )
    assert res1["status"] == "logged"
    assert "no_vehicle" in res1["identified_vulnerabilities"]

    res2 = log_311_heat_call(
        location="Garfield Park",
        vulnerabilities="Senior 85+ without air conditioning",
    )
    assert res2["status"] == "logged"
    assert "senior_no_ac" in res2["identified_vulnerabilities"]

    # Identify top vulnerabilities: must strictly return top 2
    top_vulns = identify_top_vulnerabilities(limit=2)
    assert len(top_vulns) == 2
    assert top_vulns[0]["rank"] == 1
    assert top_vulns[1]["rank"] == 2
    categories = [v["category_key"] for v in top_vulns]
    assert "senior_no_ac" in categories or "no_vehicle" in categories


def test_311_call_intake_produces_zero_tweets(tmp_path):
    """Verify that incoming 311 calls are pure vulnerability ingestion and NEVER trigger Twitter posts."""
    from social_media.call_311 import find_cooling_for_caller, intake_wellness_check, log_311_heat_call
    from social_media.twitter import TwitterClient, TwitterLedger

    # Setup isolated client with empty ledger
    ledger = TwitterLedger(storage_path=tmp_path / "test_social_feed.json")
    client = TwitterClient(ledger=ledger)

    initial_tweet_count = len(ledger.list_tweets())

    # 1. Resident calls to find cooling
    find_cooling_for_caller("63rd and Halsted")

    # 2. Resident calls to log heat vulnerability distress
    log_311_heat_call("Pilsen", vulnerabilities="Elderly resident without air conditioning")

    # 3. Resident calls to schedule a wellness check
    intake_wellness_check(
        requester_name="Citizen",
        requester_phone="312-555-1234",
        recipient_name="Elderly Neighbor",
        street_address="1800 S Blue Island Ave",
        vulnerabilities="Age 79, no vehicle, no AC",
    )

    # Assert exactly 0 tweets were published by 311 operations
    final_tweet_count = len(ledger.list_tweets())
    assert final_tweet_count == initial_tweet_count == 0
