"""build_dispatch_plan: assemble the Placement Recommendation from tool OUTPUT, not from the model's memory.

An after-tool callback (agent.py) stores the rows returned by the Toolbox tools in session state. This tool reads
them back and lays out the resources: hydration stops, wellness-check teams, library extensions. The numbers on the
sheet are therefore exactly the numbers the model produced, and the language model cannot alter a coordinate.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from google.adk.tools import ToolContext

from .config import get_settings
from .narration import describe_why

# Session-state keys written by agent.stash_tool_results
STATE_SITES = "ranked_sites"
STATE_TRACTS = "wellness_tracts"
STATE_AUDIT = "equity_audit"
STATE_PARAMS = "ranking_params"

LIBRARY_MAX_KM = 2.5


def _maps_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps?q={lat:.5f},{lon:.5f}"


def reverse_geocode(lat: float, lon: float) -> str | None:
    """Street address for a point via Google Geocoding, if a key is configured. Never raises."""
    key = get_settings().maps_api_key
    if not key:
        return None
    try:
        q = urllib.parse.urlencode({"latlng": f"{lat},{lon}", "key": key, "result_type": "street_address|route"})
        with urllib.request.urlopen(f"https://maps.googleapis.com/maps/api/geocode/json?{q}", timeout=10) as r:
            data = json.load(r)
        return data["results"][0]["formatted_address"] if data.get("status") == "OK" and data.get("results") else None
    except Exception:
        return None


def _f(row: dict, key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def pick_hydration_stops(sites: list[dict], forecast_high_f: float, n: int) -> list[dict]:
    stops: list[dict] = []
    for s in sites[:n]:
        offset = _f(s, "predicted_offset_f")
        stops.append({
            "stop": len(stops) + 1,
            "latitude": _f(s, "latitude"), "longitude": _f(s, "longitude"),
            "expected_air_temp_f": round(forecast_high_f + offset, 1),
            "predicted_offset_f": offset,
            "why_hot": describe_why(s.get("why")),
            "residents_65_plus": s.get("residents_65_plus"),
            "households_without_car": s.get("households_without_car"),
            "poverty_rate": s.get("poverty_rate"),
            "km_to_indoor_cooling": s.get("km_to_indoor_cooling"),
            "nearest_cooling": " - ".join(x for x in (s.get("nearest_cooling_name"), s.get("nearest_cooling_address")) if x),
            "cooling_readiness_status": s.get("nearest_cooling_status") or "Inventory listing; event readiness unconfirmed",
            "cooling_event_verified": s.get("nearest_cooling_event_verified") is True,
            "measured": (s.get("measured_readings") or 0) > 0,
            "measured_offset_f": s.get("measured_offset_f"),
            "tract_geo_id": s.get("tract_geo_id"), "neighborhood": s.get("neighborhood"),
        })
    return stops


def pick_libraries(sites: list[dict], n: int) -> list[dict]:
    """Distinct nearest libraries to the highest-priority sites, within walking/short-ride distance."""
    seen, out = set(), []
    for rank, s in enumerate(sites, start=1):
        name = s.get("nearest_library_name")
        km = _f(s, "km_to_library", 99)
        if not name or name in seen or km > LIBRARY_MAX_KM:
            continue
        seen.add(name)
        out.append({"library": name, "address": s.get("nearest_library_address"),
                    "current_hours": s.get("nearest_library_hours"), "km_from_site": km, "serves_site_rank": rank})
        if len(out) == n:
            break
    return out


HIGH_SCHOOL_MAX_KM = 3.5


def pick_high_schools(sites: list[dict], n: int) -> list[dict]:
    """Distinct nearest high school gymnasiums for mass emergency cooling shelter."""
    seen, out = set(), []
    for rank, s in enumerate(sites, start=1):
        name = s.get("nearest_high_school_name") or s.get("nearest_cooling_name")
        facility_type = s.get("nearest_cooling_type", "")
        if not name or name in seen:
            continue
        if s.get("nearest_high_school_name") or "High School" in facility_type or "HS" in name or "High School" in name or "Academy" in name:
            km = _f(s, "km_to_high_school", _f(s, "km_to_indoor_cooling", 99))
            if km > HIGH_SCHOOL_MAX_KM:
                continue
            seen.add(name)
            out.append({
                "high_school": name,
                "address": s.get("nearest_high_school_address") or s.get("nearest_cooling_address") or "",
                "current_hours": s.get("nearest_high_school_hours"),
                "readiness_status": s.get("nearest_high_school_status") or "Candidate; event opening, working AC and public access unconfirmed",
                "km_from_site": km,
                "serves_site_rank": rank,
            })
            if len(out) == n:
                break
    return out


def pick_wellness_teams(tracts: list[dict], n: int) -> list[dict]:
    teams: list[dict] = []
    for t in tracts[:n]:
        teams.append({
            "team": len(teams) + 1, "tract_geo_id": t.get("tract_geo_id"), "neighborhood": t.get("neighborhood"),
            "latitude": _f(t, "latitude"), "longitude": _f(t, "longitude"),
            "residents_65_plus": t.get("residents_65_plus"), "households_without_car": t.get("households_without_car"),
            "poverty_rate": t.get("poverty_rate"), "predicted_offset_f": t.get("predicted_offset_f"),
        })
    return teams


def audit_lines(audit: list[dict]) -> list[str]:
    by = {r.get("group_name"): r for r in audit}
    rec, city = by.get("recommended_sites"), by.get("citywide")
    if not rec or not city:
        return []
    lines = []
    for key, label in (("poverty_rate", "poverty rate"), ("households_without_car_rate", "households without a car"),
                       ("share_65_plus", "residents 65+"), ("mean_vulnerability_index", "vulnerability index")):
        a, b = rec.get(key), city.get(key)
        if a is not None and b:
            lines.append(f"- {label}: recommended tracts {a:.3f} vs citywide {b:.3f} ({a / b:.2f}x)")
    return lines


def render_sheet(forecast_high_f: float, stops: list[dict], teams: list[dict], libraries: list[dict],
                 audit: list[dict], params: dict, high_schools: list[dict] | None = None) -> str:
    md = [f"# Heat dispatch sheet: Chicago, forecast high {forecast_high_f:.0f}°F", ""]
    md.append("Expected air temperature = forecast high + the site's modelled offset (BigQuery ML). "
              "Offsets come from one volunteer-driven afternoon: a ranking signal, not a thermometer.")
    md.append("")
    md.append("## 1. Hydration trucks")
    if stops:
        md.append("| Stop | Stand here | Expected air temp | Why it is hot | Who is nearby | Nearest indoor cooling |")
        md.append("|---|---|---|---|---|---|")
        for s in stops:
            where = (f"**{s['neighborhood']}**<br>" if s.get("neighborhood") else "") + \
                f"[{s['latitude']:.5f}, {s['longitude']:.5f}]({_maps_link(s['latitude'], s['longitude'])})"
            if s.get("approx_address"):
                where += f"<br>{s['approx_address']}"
            who = f"{s.get('residents_65_plus', '?')} aged 65+, {s.get('households_without_car', '?')} homes w/o car"
            near = f"{s['nearest_cooling'] or 'n/a'} ({s.get('km_to_indoor_cooling', '?')} km)"
            if not s.get("cooling_event_verified"):
                near += " — event readiness unconfirmed"
            temp = f"{s['expected_air_temp_f']}°F ({s['predicted_offset_f']:+.1f})"
            tag = "" if s["measured"] else " *(model prediction, never measured)*"
            md.append(f"| {s['stop']} | {where} | {temp}{tag} | {s['why_hot'] or 'n/a'} | {who} | {near} |")
    else:
        md.append("_No hydration stops: call rank_priority_sites first._")
    md += ["", "## 2. Wellness checks"]
    if teams:
        for t in teams:
            md.append(f"- Team {t['team']}: {t.get('neighborhood') or 'tract'} (tract {t['tract_geo_id']}) ([map]({_maps_link(t['latitude'], t['longitude'])})), "
                      f"{t.get('residents_65_plus', '?')} residents 65+, {t.get('households_without_car', '?')} households "
                      f"without a car, predicted offset {t.get('predicted_offset_f', '?')}°F")
    else:
        md.append("_No wellness teams: call rank_wellness_check_tracts first._")
    md += ["", "## 3. Extended library hours"]
    if libraries:
        for lb in libraries:
            md.append(f"- **{lb['library']}**, {lb['address']}: serves site #{lb['serves_site_rank']} "
                      f"({lb['km_from_site']} km). Current hours: {lb['current_hours']}. Confirm extension with CPL.")
    else:
        md.append("_No library within 2.5 km of a recommended stop._")
    if high_schools:
        md += ["", "## 4. High school cooling candidates"]
        for hs in high_schools:
            md.append(f"- **{hs['high_school']}**, {hs['address']}: serves site #{hs['serves_site_rank']} "
                      f"({hs['km_from_site']} km). {hs.get('readiness_status', 'Event readiness unconfirmed')}. "
                      f"Listed hours: {hs['current_hours'] or 'unknown'}.")
    md += ["", "## Equity check on this plan"]
    lines = audit_lines(audit)
    md += lines if lines else ["_Not yet run: call equity_audit with the same parameters before sending this plan._"]
    md += ["", "## Basis and limits",
           f"- Priority = (heat_weight {params.get('heat_weight', 0.5)} x heat percentile + rest x vulnerability) x access to indoor cooling. "
           "Vulnerability = 65+, no car, poverty (ACS). Race is not used.",
           "- Heat model: BigQuery ML linear regression on physical features only (lake, downtown, density, housing age).",
           "- Facility data: City of Chicago Cooling Centers snapshot; confirm hours before publishing.",
           "- This is decision support for a dispatcher, not an official advisory or evacuation order."]
    return "\n".join(md)


def build_dispatch_plan(forecast_high_f: float, hydration_trucks: int = 3, wellness_teams: int = 4,
                        library_extensions: int = 3, tool_context: ToolContext | None = None,
                        emergency_gyms: int = 2, sites: list[dict] | None = None,
                        tracts: list[dict] | None = None) -> dict:
    """Assemble the dispatch sheet (the Placement Recommendation) from the rankings already fetched this session.

    Call rank_priority_sites first (and rank_wellness_check_tracts and equity_audit if you can). This tool reads
    their results from session state, so coordinates and numbers are exactly what the model produced.

    Args:
        forecast_high_f: forecast high for the planning day in degrees F.
        hydration_trucks: how many trucks the city can field.
        wellness_teams: how many wellness-check teams.
        library_extensions: how many libraries could stay open longer.
        tool_context: injected ADK ToolContext holding session state.
        emergency_gyms: how many high school gymnasiums to designate as backup cooling shelters.
        sites: optional list of priority site dicts if already in context.
        tracts: optional list of wellness check tract dicts if already in context.
    """
    state: Any = tool_context.state if tool_context is not None else {}
    sites = list(sites or state.get(STATE_SITES) or [])
    if not sites:
        return {"status": "need_data", "message": "No ranked sites in this session yet. Call rank_priority_sites first."}
    tracts = list(tracts or state.get(STATE_TRACTS) or [])
    audit = list(state.get(STATE_AUDIT) or [])
    params = dict(state.get(STATE_PARAMS) or {})

    stops = pick_hydration_stops(sites, float(forecast_high_f), max(0, int(hydration_trucks)))
    for s in stops:                       # street address when a Maps key is configured (best effort)
        s["approx_address"] = reverse_geocode(s["latitude"], s["longitude"])
    teams = pick_wellness_teams(tracts, max(0, int(wellness_teams)))
    libraries = pick_libraries(sites, max(0, int(library_extensions)))
    high_schools = pick_high_schools(sites, max(0, int(emergency_gyms)))

    markers = (
        [{"lat": s["latitude"], "lng": s["longitude"], "kind": "hydration",
          "label": f"Truck {s['stop']}{' · ' + s['neighborhood'] if s.get('neighborhood') else ''}: {s['expected_air_temp_f']}°F expected"} for s in stops]
        + [{"lat": t["latitude"], "lng": t["longitude"], "kind": "wellness",
            "label": f"Wellness team {t['team']} · {t.get('neighborhood') or t['tract_geo_id']}"} for t in teams]
    )
    preview_tweets = []
    try:
        from social_media.vulnerability import identify_top_vulnerabilities
        from social_media.dispatch_updates import format_top_vulnerabilities_tweet
        top2 = identify_top_vulnerabilities({"wellness_teams": teams, "hydration_stops": stops}, limit=2)
        if top2:
            preview_tweets.append(format_top_vulnerabilities_tweet(top2))
    except Exception:
        pass

    for s in stops:
        preview_tweets.append(f"🚛 Action: Hydration Truck #{s['stop']} deployed to {s.get('neighborhood', 'Chicago site')} ({s['latitude']:.4f}, {s['longitude']:.4f})")
    for lib in libraries:
        preview_tweets.append(f"❄️ Relief: Extended hours at {lib['library']} ({lib['address']})")
    if teams:
        preview_tweets.append(f"💜 Outreach: {len(teams)} wellness teams active in priority neighborhoods")

    return {
        "status": "ok",
        "dispatch_sheet_markdown": render_sheet(float(forecast_high_f), stops, teams, libraries, audit, params, high_schools),
        "hydration_stops": stops, "wellness_teams": teams, "library_extensions": libraries,
        "high_school_gyms": high_schools,
        "equity_audit_ran": bool(audit),
        "map": {"markers": markers},
        "social_media_preview": preview_tweets,
    }
