# Event-date weather and scheduled transit

`scripts/prepare_event_data.py` prepares a specific event from the acquired CTA ZIP
and NWS hourly JSONL. It makes no network calls or cloud writes. Each run writes an
immutable directory under `data/enrichment/event/EVENT_DATE/RUN_TIMESTAMP/`.

## Prepared November 17 snapshot

Run `20261117T220247670918Z` used reference time **2026-11-17 22:02:47 UTC**:

| Output | Rows | Meaning |
|---|---:|---|
| `event_transit_departures.jsonl` | 263,715 | Scheduled stop/route/service-hour aggregates occurring on the actual event clock date |
| `event_transit_validity.jsonl` | 1 | Calendar coverage, freshness, source hash and service-date audit |
| `event_weather_hourly.jsonl` | 102 | Forecast periods overlapping the event date and following overnight window |
| `event_weather_summary.jsonl` | 6 | One coherent forecast snapshot and coverage summary per representative NWS grid |
| `event_data_validity.jsonl` | 1 | Event-wide coverage/freshness gates and explicit lack of supplied readiness confirmation |

The transit rows contain **5,218 aggregates from November 16's service date** continuing into
November 17. Total scheduled stop departures are 1,224,027. This count repeats a trip at each
served stop; it is not a count of unique vehicles, trips or residents. Both source service dates
are retained. CTA calendar coverage runs October 29–December 31, 2026.

Each NWS grid has:

- **11/24 hours** of the event civil day, with 13 hours missing.
- **9/16 hours** of the 06:00–22:00 daytime window, with 7 hours missing.
- **8/8 hours** of the 22:00–06:00 following overnight window.

The cached forecast starts at 13:00 local on November 17. `forecast_high_f`, `forecast_low_f`
and `daytime_high_f` are therefore **null**. Available-period maxima remain separately named
`available_civil_day_max_f` and `available_daytime_max_f`; they cannot represent a complete-day high.
The complete overnight windows have forecast lows of 40–43°F across the six grids.

The weather snapshot was retrieved at 19:30:12 UTC; the transit feed at 19:30:34 UTC.
They were approximately 2.54 hours old at preparation reference time. The overall validity row
allows the overnight forecast low, and rejects publication of a full civil-day or daytime high.
All temperatures here are November forecasts, not historical Heat Watch observations.

## GTFS time convention and previous service date

GTFS departure times measure elapsed seconds from **local noon minus twelve elapsed hours**.
The script performs that subtraction in UTC, then adds the raw GTFS seconds. Using ordinary
local midnight as the origin is incorrect on daylight-saving transition days.

Both the event service date and the previous service date are normalized from the official
cached ZIP with the existing `normalize_feed` function. Departures are retained only when their
converted local clock date equals the event date. The outputs preserve:

- Original `service_date`, `service_hour`, departure seconds and scheduled counts.
- Actual local date/hour, ISO local departure timestamps with UTC offsets, and `local_fold`.
- UTC bucket and first/last departure timestamps.
- Whether the row came from the previous service date.
- Original source retrieval time, independently of the new preparation time.

For Chicago's fall 2026 clock change, GTFS service hour 0 maps to the first 01:00 hour and
service hour 1 to the repeated 01:00 hour; the preceding service date contributes midnight.
For the spring clock change, service hour 0 can map to the preceding civil date at 23:00.
UTC timestamps and `local_fold` prevent repeated local hours from collapsing.

The transit table's unique source key is:

```text
(event_date, service_date, stop_id, route_id, service_hour)
```

Two service dates can legitimately contribute to the same actual stop/route/clock-hour bucket.
Retain the service date in source joins, or aggregate scheduled counts by
`(event_date, stop_id, route_id, bucket_start_utc)` before joining a single clock-hour context row.
Do not deduplicate those legitimate trips solely by local hour.

The inherited normalization excludes `pickup_type=1` and frequency-template trips, records
missing/invalid times, and applies calendar exceptions. This remains scheduled service, with
no claim about live arrivals, closures, elevator access, trip reachability or historical traffic.
CTA's purpose-limited developer terms apply; the intended use is resident transit access to cooling.

## Weather windows and one coherent snapshot

Event windows are explicitly defined in `America/Chicago`:

- Civil day: local midnight through next local midnight.
- Daytime: 06:00–22:00 on the event date.
- Overnight: 22:00 on the event date through 06:00 the following day.

The previously acquired `weather_daily` table uses a different overnight window, 20:00–08:00.
The event outputs intentionally label their 22:00–06:00 definition rather than silently treating
the two summaries as interchangeable. Coverage uses elapsed UTC duration, so a DST civil day
can have 23 or 25 hours and an overnight window 7 or 9 hours.

For each grid, the script selects the latest coherent tuple of retrieval time, issue time,
generation time and source URL. Exact duplicate periods within that snapshot are removed.
Conflicting duplicate periods are rejected. Older retrievals cannot fill gaps in a newer snapshot.
Missing temperature values do not contribute to temperature coverage; overlapping intervals
are merged before hours are counted.

The event hourly key is:

```text
(event_date, grid_key, valid_start)
```

There is one summary per event/grid, keeping issue, generation, retrieval and reference times
alongside missing hours and completeness. Humidity, dew point, wind and forecast text remain
the original hourly source fields; no missing values are fabricated.

## Freshness and unavailable dates

Default maximum source age at the reference time is **6 hours for weather** and **168 hours for
the CTA static feed**. These are explicit configurable planning policies, not source guarantees.
Retrievals or forecasts issued after the reference time cannot be considered fresh for that
reference. CTA retrieval time is trusted only when the cached manifest hash matches the ZIP.
Otherwise freshness is `unknown_source_retrieval`.

Coverage and freshness are reported separately. Complete but stale forecast windows retain
their source values for provenance; the event-wide publication gates are false. Consumers should
check `can_publish_civil_day_forecast_high`, `can_publish_daytime_high` or
`can_publish_overnight_low` before presenting an event-wide value as current.

An event outside the cached forecast or transit coverage still produces validity records and
explicit per-grid missing-window summaries. Hourly/departure files can be empty. No placeholder
forecast, traffic observation or operational readiness is invented to fill them. The event module
supplies no facility confirmation; consult the separate event readiness records for actual status.

## Reproduce and publish

```bash
.venv/bin/python scripts/prepare_event_data.py --event-date 2026-11-17
```

Source paths default to `data/enrichment/transit/google_transit.zip` and
`data/enrichment/weather/weather_hourly.jsonl`. Optional arguments select other snapshots,
an explicit reference timestamp, source age limits or an output parent directory.
All five output schemas are exported in `SCHEMAS` for the parent work-dataset publisher.
`manifest.json` stores source hashes, output hashes/counts, reference time and event validity.
Preparation never overwrites an earlier date/run directory.

The script itself does not publish. A publisher should preserve other event dates and handle empty
data files through their explicit validity state; leaving an older event's rows as if they cover an
unavailable new date would defeat the date filtering.

## Verification

Ten focused tests pass, covering GTFS noon-origin conversion at both DST changes, previous-service
spillover and join keys, repeated local hours, coherent weather snapshot deduplication, conflicting
duplicates, complete/partial/missing-temperature windows, DST coverage duration, source freshness
and dates outside source coverage. Mypy passes for the new script. Preparation validates explicit
schemas and unique event-period/source-service keys before writing artifacts.

References:

- [GTFS schedule reference](https://gtfs.org/documentation/schedule/reference/)
- [CTA scheduled data](https://www.transitchicago.com/developers/gtfs/)
- [CTA terms](https://www.transitchicago.com/developers/terms/)
- [NWS API documentation](https://www.weather.gov/documentation/services-web-api)
- [Weather source conventions](weather.md)
