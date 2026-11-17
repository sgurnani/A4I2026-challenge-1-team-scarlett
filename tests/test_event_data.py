import csv
import datetime as dt
import io
import zipfile

import pytest

from load_chicago_weather import HOURLY_SCHEMA
from prepare_event_data import (
    CHICAGO,
    event_departures,
    freshness,
    gtfs_time,
    prepare_transit,
    prepare_weather,
    validity_summary,
    window_bounds,
)

UTC = dt.timezone.utc
PREPARED = "2026-11-17T12:00:00+00:00"
REFERENCE = dt.datetime(2026, 11, 17, 12, tzinfo=UTC)
EVENT = dt.date(2026, 11, 17)


def raw_departure(service_date, hour, count=1):
    return {"service_date": service_date, "stop_id": "001", "route_id": "R",
            "service_hour": hour, "scheduled_departures": count,
            "first_departure_seconds": hour * 3600 + 600,
            "last_departure_seconds": hour * 3600 + 1800, "source": "CTA"}


def period(start, temperature=90, retrieved="2026-11-17T11:00:00+00:00", key="LOT/1,1"):
    row = {name: None for name, _ in HOURLY_SCHEMA}
    local = start.astimezone(CHICAGO)
    row.update(grid_key=key, point_names="Test", latitude=41.88, longitude=-87.63,
               valid_start=start.isoformat(), valid_end=(start + dt.timedelta(hours=1)).isoformat(),
               local_date=local.date().isoformat(), local_hour=local.hour, temperature_f=temperature,
               retrieved_at=retrieved, forecast_issued_at="2026-11-17T10:00:00+00:00",
               forecast_generated_at="2026-11-17T10:30:00+00:00", source="NWS")
    return row


def sample_gtfs():
    files = {
        "stops.txt": [{"stop_id": "001", "stop_name": "Test", "stop_lat": "41.88", "stop_lon": "-87.63"}],
        "routes.txt": [{"route_id": "R", "route_type": "3"}],
        "calendar.txt": [{"service_id": name, "start_date": date, "end_date": date,
                           **{day: "1" for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}}
                          for name, date in [("previous", "20261116"), ("event", "20261117")]],
        "trips.txt": [{"trip_id": "P", "route_id": "R", "service_id": "previous"},
                      {"trip_id": "E", "route_id": "R", "service_id": "event"}],
        "stop_times.txt": [{"trip_id": "P", "stop_id": "001", "departure_time": "25:10:00", "pickup_type": "0"},
                           {"trip_id": "E", "stop_id": "001", "departure_time": "01:20:00", "pickup_type": "0"},
                           {"trip_id": "E", "stop_id": "001", "departure_time": "01:30:00", "pickup_type": "1"}],
    }
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, rows in files.items():
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(name, buffer.getvalue())
    return payload.getvalue()


def test_gtfs_noon_origin_handles_spring_and_fall_dst():
    spring = dt.date(2026, 3, 8)
    assert gtfs_time(spring, 0).astimezone(CHICAGO).isoformat() == "2026-03-07T23:00:00-06:00"
    assert gtfs_time(spring, 3 * 3600).astimezone(CHICAGO).isoformat() == "2026-03-08T03:00:00-05:00"
    fall = dt.date(2026, 11, 1)
    first = gtfs_time(fall, 0).astimezone(CHICAGO)
    second = gtfs_time(fall, 3600).astimezone(CHICAGO)
    assert first.hour == second.hour == 1
    assert first.fold == 0 and second.fold == 1
    assert first.utcoffset() != second.utcoffset()


def test_previous_service_date_and_event_date_are_both_counted_without_duplicate_join_keys():
    rows, validity = prepare_transit(sample_gtfs(), EVENT, "2026-11-17T11:00:00+00:00", REFERENCE, PREPARED)
    assert len(rows) == 2
    assert {row["service_date"] for row in rows} == {"2026-11-16", "2026-11-17"}
    assert {row["actual_local_hour"] for row in rows} == {1}
    assert sum(row["scheduled_departures"] for row in rows) == 2
    assert validity["previous_day_spillover_rows"] == 1
    assert validity["scheduled_departures_total"] == 2
    assert len({(row["service_date"], row["stop_id"], row["route_id"], row["service_hour"]) for row in rows}) == 2


def test_fall_event_midnight_comes_from_previous_service_date_and_repeated_hours_keep_fold():
    rows = event_departures([raw_departure("2026-10-31", 24), raw_departure("2026-11-01", 0),
                             raw_departure("2026-11-01", 1)], dt.date(2026, 11, 1), None, PREPARED)
    assert [(row["actual_local_hour"], row["local_fold"]) for row in rows] == [(0, 0), (1, 0), (1, 1)]
    assert len({row["bucket_start_utc"] for row in rows}) == 3


def test_latest_weather_snapshot_does_not_fill_gaps_with_older_retrieval_and_deduplicates_exact_rows():
    start = window_bounds(EVENT)["civil_day"][0]
    older = period(start, 100, retrieved="2026-11-17T10:00:00+00:00")
    newest = period(start + dt.timedelta(hours=1), 80)
    hourly, summaries = prepare_weather([older, newest, dict(newest)], EVENT, REFERENCE, PREPARED)
    assert len(hourly) == 1
    assert summaries[0]["civil_day_covered_hours"] == 1
    assert summaries[0]["older_snapshot_rows_excluded"] == 1
    assert summaries[0]["duplicate_rows_removed"] == 1
    assert summaries[0]["available_civil_day_max_f"] == 80
    assert summaries[0]["forecast_high_f"] is None
    assert summaries[0]["daytime_high_f"] is None


def test_conflicting_duplicate_weather_periods_are_rejected():
    start = window_bounds(EVENT)["civil_day"][0]
    with pytest.raises(ValueError, match="conflicting duplicate"):
        prepare_weather([period(start, 80), period(start, 100)], EVENT, REFERENCE, PREPARED)


def test_complete_civil_day_and_overnight_weather_keep_one_coherent_retrieval():
    bounds = window_bounds(EVENT)
    start, end = bounds["civil_day"][0], bounds["overnight"][1]
    rows = [period(start + dt.timedelta(hours=hour), 80 + hour) for hour in range(int((end - start).total_seconds() / 3600))]
    hourly, summaries = prepare_weather(rows, EVENT, REFERENCE, PREPARED)
    summary = summaries[0]
    assert len(hourly) == 30
    assert summary["civil_day_complete"] and summary["daytime_complete"] and summary["overnight_complete"]
    assert summary["forecast_high_f"] == 103
    assert summary["forecast_low_f"] == 80
    assert summary["daytime_high_f"] == 101
    assert summary["overnight_low_f"] == 102
    assert {row["retrieved_at"] for row in hourly} == {summary["source_retrieved_at"]}


def test_missing_temperature_does_not_count_as_forecast_coverage():
    bounds = window_bounds(EVENT)
    start = bounds["civil_day"][0]
    rows = [period(start + dt.timedelta(hours=hour)) for hour in range(30)]
    rows[10]["temperature_f"] = None
    _, summaries = prepare_weather(rows, EVENT, REFERENCE, PREPARED)
    assert summaries[0]["civil_day_missing_hours"] == 1
    assert summaries[0]["forecast_high_f"] is None
    assert summaries[0]["daytime_high_f"] is None


def test_day_and_overnight_expected_hours_use_elapsed_time_at_dst():
    fall_day = window_bounds(dt.date(2026, 11, 1))["civil_day"]
    fall_night = window_bounds(dt.date(2026, 10, 31))["overnight"]
    spring_day = window_bounds(dt.date(2026, 3, 8))["civil_day"]
    spring_night = window_bounds(dt.date(2026, 3, 7))["overnight"]
    assert (fall_day[1] - fall_day[0]).total_seconds() / 3600 == 25
    assert (fall_night[1] - fall_night[0]).total_seconds() / 3600 == 9
    assert (spring_day[1] - spring_day[0]).total_seconds() / 3600 == 23
    assert (spring_night[1] - spring_night[0]).total_seconds() / 3600 == 7


def test_stale_and_after_reference_sources_are_explicitly_ineligible_for_publication():
    assert freshness("2026-11-16T00:00:00Z", REFERENCE, 6)[1] == "stale"
    assert freshness("2026-11-18T00:00:00Z", REFERENCE, 6)[1] == "retrieved_after_reference"
    bounds = window_bounds(EVENT)
    rows = [period(bounds["civil_day"][0] + dt.timedelta(hours=hour), retrieved="2026-11-16T00:00:00Z") for hour in range(30)]
    _, summaries = prepare_weather(rows, EVENT, REFERENCE, PREPARED)
    status = validity_summary(EVENT, summaries, {"event_status": "available", "freshness_status": "fresh",
                              "source_retrieved_at": None, "event_departure_rows": 0}, REFERENCE, PREPARED, expected_grids=1)
    assert status["civil_day_complete_grid_count"] == 1
    assert not status["can_publish_civil_day_forecast_high"]
    assert not status["can_publish_overnight_low"]


def test_outside_source_dates_still_produce_explicit_validity_records():
    outside = dt.date(2027, 1, 1)
    transit, transit_status = prepare_transit(sample_gtfs(), outside, None, REFERENCE, PREPARED)
    hourly, summaries = prepare_weather([period(window_bounds(EVENT)["civil_day"][0])], outside, REFERENCE, PREPARED)
    status = validity_summary(outside, summaries, transit_status, REFERENCE, PREPARED, expected_grids=1)
    assert not transit and not hourly
    assert transit_status["event_status"] == "outside_feed_coverage"
    assert summaries[0]["coverage_status"] == "outside_forecast_coverage"
    assert status["weather_status"] == "outside_forecast_coverage_or_missing_source"
    assert not status["can_publish_civil_day_forecast_high"]
