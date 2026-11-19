"""Deterministic dispatch planner shared by the agent (chat re-plans) and the web backend (slider re-plans).

Input: the operator's constraints, rows returned by the MCP Toolbox tools (rank_priority_sites,
rank_wellness_check_tracts, list_sites), and the operator's approvals / skips. Output: plan items with stable keys,
a diff against the previous plan, and capacity warnings. Every number on an item is copied from a tool row; this
module only selects, filters and orders. The language model never writes a number into the plan.

STDLIB ONLY, NO RELATIVE IMPORTS: the web backend loads this file by path without importing the ADK agent package.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

KINDS = ("well", "truck", "lib")
LABEL = {"well": "wellness check team", "truck": "hydration truck", "lib": "library extended hours"}
RESOURCE_OF = {"well": "wellness_teams", "truck": "trucks", "lib": "libraries"}

# Rank-by presets -> heat_weight passed to the ranking tools (priority = w*heat + (1-w)*vulnerability, x access).
RANKING = {"balanced": 0.5, "heat": 1.0, "vulnerable": 0.2}

DEFAULT_CONSTRAINTS: dict[str, Any] = {
    "trucks": 10, "wellness_teams": 8, "libraries": 6,
    "closed_sites": [], "cutoff_f": 0.0, "ranking": "balanced",
}
LIMITS = {"trucks": 40, "wellness_teams": 40, "libraries": 20}
CUTOFF_RANGE = (-3.0, 3.0)

LIB_RADIUS_KM = 1.2          # a library "serves" ranked tracts whose centre is within this walk
EXTEND_TO_MIN = 22 * 60      # extend library hours to 10 pm
SITES_FETCH = 50             # rank_priority_sites rows fetched (headroom for skips/closures)
TRACTS_FETCH = 120           # rank_wellness_check_tracts rows fetched (wellness + library catchments)

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# ---------------------------------------------------------------------------------------------- constraints
def normalize_constraints(c: dict | None) -> dict:
    """Fill defaults, clamp ranges, reject unknown ranking presets (falls back to balanced)."""
    out = {**DEFAULT_CONSTRAINTS, **{k: v for k, v in (c or {}).items() if v is not None}}
    for k, mx in LIMITS.items():
        out[k] = max(0, min(mx, int(out[k])))
    out["cutoff_f"] = round(max(CUTOFF_RANGE[0], min(CUTOFF_RANGE[1], float(out["cutoff_f"]))), 2)
    if out["ranking"] not in RANKING:
        out["ranking"] = "balanced"
    out["closed_sites"] = sorted({str(s) for s in out.get("closed_sites") or []})
    return {k: out[k] for k in DEFAULT_CONSTRAINTS}


def ranking_calls(constraints: dict) -> dict[str, dict]:
    """The Toolbox calls (tool name -> parameters) a plan needs. Scoring stays in BigQuery (ML.EXPLAIN_PREDICT)."""
    c = normalize_constraints(constraints)
    w = RANKING[c["ranking"]]
    return {
        "rank_priority_sites": {"limit": SITES_FETCH, "heat_weight": w, "min_offset": c["cutoff_f"]},
        "rank_wellness_check_tracts": {"limit": TRACTS_FETCH, "heat_weight": w, "min_offset": c["cutoff_f"]},
    }


def resolve_site_names(names: Iterable[str], known: Iterable[str]) -> tuple[list[str], list[str]]:
    """Match operator-typed names ('Legler', 'legler regional library') to known site names. -> (matched, unknown)."""
    known = list(known)
    norm = {k: _norm(k) for k in known}
    matched, unknown = [], []
    for raw in names:
        q = _norm(raw)
        hits = [k for k, n in norm.items() if q and (q == n or q in n or n in q)]
        exact = [k for k in hits if norm[k] == q]
        pick = exact or hits
        if len(pick) == 1:
            matched.append(pick[0])
        else:
            unknown.append(raw)
    return matched, unknown


def _norm(s: str) -> str:
    s = re.sub(r"\b(regional|library|branch|center|centre|the)\b", " ", str(s).lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------------------------------------------------------------------------------------------- library hours
_DAY_RE = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?"
_GROUP_RE = re.compile(rf"({_DAY_RE}(?:\s*(?:&|-|and|to)\s*{_DAY_RE})*)\s*,\s*(noon|\d{{1,2}})\s*-\s*(noon|\d{{1,2}})", re.I)


def parse_hours(text: str | None) -> dict[str, tuple[int, int]]:
    """CPL service hours ('Mon. & Wed., Noon-8; Tue. & Thu., 10-6; ...') -> {day: (open_min, close_min)}.

    Unknown or 'Closed until further notice' -> {} (treated as closed every day)."""
    out: dict[str, tuple[int, int]] = {}
    for days_txt, a, b in _GROUP_RE.findall(text or ""):
        o, cl = _hour(a, opening=True), _hour(b, opening=False)
        for d in _expand_days(days_txt):
            out[d] = (o, cl)
    return out


def _hour(tok: str, opening: bool) -> int:
    if tok.lower() == "noon":
        return 12 * 60
    h = int(tok)
    if opening:
        return (h + 12 if h < 8 else h) * 60      # '1' (Sun 1-5) is 1 pm; '9', '10' are am
    return (h + 12 if h < 12 else h) * 60         # closing times are pm


def _expand_days(txt: str) -> list[str]:
    toks = [m.group(0)[:3].lower() for m in re.finditer(_DAY_RE, txt, re.I)]
    if re.search(r"-|\bto\b", txt) and len(toks) == 2:
        i, j = DAYS.index(toks[0]), DAYS.index(toks[1])
        return list(DAYS[i:j + 1])
    return toks


def fmt_minutes(m: int) -> str:
    h, mm = divmod(int(m), 60)
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{':%02d' % mm if mm else ''} {suffix}"


def hours_today(text: str | None, weekday: str) -> str | None:
    span = parse_hours(text).get(weekday[:3].lower())
    return f"{fmt_minutes(span[0])}-{fmt_minutes(span[1])}" if span else None


# ---------------------------------------------------------------------------------------------- geometry
def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _f(row: dict, key: str, default: float | None = 0.0) -> float | None:
    try:
        v = row.get(key)
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


def why_pairs(why: Any) -> list[dict]:
    """MCP returns structs as [name, value] pairs or objects; normalise to [{feature, contribution_f}]."""
    out = []
    for w in why or []:
        if isinstance(w, dict):
            out.append({"feature": w.get("feature"), "contribution_f": w.get("contribution_f")})
        elif isinstance(w, (list, tuple)):
            d = dict(x for x in w if isinstance(x, (list, tuple)) and len(x) == 2) if w and isinstance(w[0], (list, tuple)) else {}
            if not d and len(w) == 2:
                d = {"feature": w[0], "contribution_f": w[1]}
            out.append({"feature": d.get("feature"), "contribution_f": d.get("contribution_f")})
    return out


# ---------------------------------------------------------------------------------------------- items
def truck_item(s: dict) -> dict:
    measured = (s.get("measured_readings") or 0) > 0
    return {
        "key": f"truck:{s.get('cell_id')}", "kind": "truck",
        "name": s.get("neighborhood") or f"cell {s.get('cell_id')}", "neighborhood": s.get("neighborhood"),
        "latitude": _f(s, "latitude"), "longitude": _f(s, "longitude"), "tract_geo_id": s.get("tract_geo_id"),
        "priority_score": _f(s, "priority_score"), "predicted_offset_f": _f(s, "predicted_offset_f"),
        "measured": measured, "measured_offset_f": s.get("measured_offset_f"), "why": why_pairs(s.get("why")),
        "residents_65_plus": s.get("residents_65_plus"), "households_without_car": s.get("households_without_car"),
        "poverty_rate": s.get("poverty_rate"), "tract_population": s.get("tract_population"),
        "km_to_indoor_cooling": s.get("km_to_indoor_cooling"), "nearest_cooling_name": s.get("nearest_cooling_name"),
        "vulnerability_index": s.get("vulnerability_index"),
    }


def well_item(t: dict) -> dict:
    return {
        "key": f"well:{t.get('tract_geo_id')}", "kind": "well",
        "name": t.get("neighborhood") or f"tract {t.get('tract_geo_id')}", "neighborhood": t.get("neighborhood"),
        "latitude": _f(t, "latitude"), "longitude": _f(t, "longitude"), "tract_geo_id": t.get("tract_geo_id"),
        "priority_score": _f(t, "priority_score"), "predicted_offset_f": _f(t, "predicted_offset_f"),
        "residents_65_plus": t.get("residents_65_plus"), "households_without_car": t.get("households_without_car"),
        "poverty_rate": t.get("poverty_rate"), "tract_population": t.get("tract_population"),
        "km_to_indoor_cooling": t.get("avg_km_to_indoor_cooling"), "vulnerability_index": t.get("vulnerability_index"),
    }


def library_candidates(libraries: list[dict], tracts: list[dict], taken: set[str], closed: set[str],
                       weekday: str) -> list[dict]:
    """Open-today libraries scored by the priority of ranked tracts within LIB_RADIUS_KM not already given a team."""
    out = []
    for lb in libraries:
        name = lb.get("site_name")
        if not name or name in closed:
            continue
        span = parse_hours(lb.get("service_hours") or lb.get("hours_of_operation")).get(weekday[:3].lower())
        if not span:
            continue
        lat, lon = _f(lb, "latitude"), _f(lb, "longitude")
        served = [t for t in tracts if t.get("tract_geo_id") not in taken
                  and km_between(lat, lon, _f(t, "latitude"), _f(t, "longitude")) <= LIB_RADIUS_KM]
        if not served:
            continue
        out.append({
            "key": f"lib:{name}", "kind": "lib", "name": name, "address": lb.get("address"),
            "latitude": lat, "longitude": lon,
            "priority_score": round(sum(_f(t, "priority_score") for t in served), 3),
            "hours_today": f"{fmt_minutes(span[0])}-{fmt_minutes(span[1])}",
            "extend_to": fmt_minutes(EXTEND_TO_MIN) if span[1] < EXTEND_TO_MIN else None,
            "served_tracts": [t.get("tract_geo_id") for t in served],
            "served_population": int(sum(_f(t, "tract_population") for t in served)),
            "served_65_plus": int(sum(_f(t, "residents_65_plus") for t in served)),
            "radius_km": LIB_RADIUS_KM,
        })
    out.sort(key=lambda b: -b["priority_score"])
    return out


def _take(cands: list[dict], cap: int, approved: dict, skipped: set[str], kind: str) -> list[dict]:
    """Approved items first (they stay pinned), then the best unskipped candidates, up to cap."""
    by_key = {c["key"]: c for c in cands}
    pinned = [by_key[k] for k in approved if k.startswith(kind + ":") and k in by_key]
    pinned.sort(key=lambda c: -(c.get("priority_score") or 0))
    chosen = pinned[:cap]
    keys = {c["key"] for c in chosen}
    for c in cands:
        if len(chosen) >= cap:
            break
        if c["key"] in keys or c["key"] in skipped:
            continue
        chosen.append(c)
        keys.add(c["key"])
    return chosen


def enforce_capacity(constraints: dict, approved: dict, candidates_by_kind: dict[str, list[dict]],
                     closed: set[str]) -> tuple[dict, list[dict]]:
    """Un-approve what can no longer be honoured. Returns (approved', warnings).

    - more approved than capacity -> drop the lowest-priority approvals (the mockup's behaviour);
    - approved library now closed, or approved item no longer in the candidate rows (e.g. cutoff raised) -> drop."""
    c = normalize_constraints(constraints)
    keep = dict(approved)
    warnings: list[dict] = []
    for kind in KINDS:
        cands = {x["key"]: x for x in candidates_by_kind.get(kind, [])}
        mine = [k for k in keep if k.startswith(kind + ":")]
        gone = []
        for k in mine:
            if kind == "lib" and k.split(":", 1)[1] in closed:
                gone.append((k, "closed"))
            elif k not in cands:
                gone.append((k, "no longer qualifies"))
        for k, why in gone:
            keep.pop(k, None)
        if gone:
            def _item_label(k: str, w: str) -> str:
                nm = (approved.get(k) or {}).get("name") or _name(k)
                return f"{nm} ({w})" if w != "no longer qualifies" else nm

            labels = [_item_label(k, w) for k, w in gone]
            summary_str = ", ".join(labels[:3]) + (f", and {len(labels) - 3} more" if len(labels) > 3 else "")
            warnings.append({"kind": kind, "reason": "ineligible", "unapproved": [k for k, _ in gone],
                             "message": f"Un-approved {len(gone)} {LABEL[kind]} item(s) that no longer qualify: {summary_str}"})
        mine = [k for k in keep if k.startswith(kind + ":")]
        cap = c[RESOURCE_OF[kind]]
        if len(mine) > cap:
            mine.sort(key=lambda k: -(cands.get(k, {}).get("priority_score") or 0))
            drop = mine[cap:]
            for k in drop:
                keep.pop(k, None)
            drop_labels = [(approved.get(k) or {}).get("name") or cands.get(k, {}).get("name") or _name(k) for k in drop]
            drop_str = ", ".join(drop_labels[:3]) + (f", and {len(drop_labels) - 3} more" if len(drop_labels) > 3 else "")
            warnings.append({"kind": kind, "reason": "capacity", "unapproved": drop,
                             "message": f"You had approved {len(mine)} {LABEL[kind]} item(s) but only {cap} are "
                                        f"available. Un-approved the {len(drop)} lowest-priority: {drop_str}"})
    return keep, warnings


def _name(key: str) -> str:
    return key.split(":", 1)[1]


def build_plan(constraints: dict, sites: list[dict], tracts: list[dict], libraries: list[dict],
               approved: dict | None = None, skipped: Iterable[str] = (), weekday: str = "mon",
               day: dict | None = None) -> dict:
    """Assemble a plan. `approved` maps item key -> approval record; it is returned pruned by capacity rules."""
    c = normalize_constraints(constraints)
    if day and day.get("high_f") is not None and day.get("high_f") < 80:
        return {
            "constraints": c,
            "weekday": weekday[:3].lower(),
            "items": [],
            "approved": {},
            "skipped": [],
            "warnings": [],
            "stand_down": True,
            "stand_down_reason": f"Normal weather ({day['high_f']}°F < 80°F). No heat emergency dispatch required. Mobile hydration trucks, wellness teams, and extended cooling library hours are standing down.",
        }
    closed = set(c["closed_sites"])
    skipped = set(skipped)
    approved = dict(approved or {})

    # wellness: highest-priority tracts (rows arrive ranked from BigQuery)
    well_c = [well_item(t) for t in tracts]
    # library catchments may not use a tract that is a wellness CANDIDATE already chosen; compute after wellness pick
    truck_c = [truck_item(s) for s in sites]

    # Capacity/eligibility is checked against candidates computed without approvals affecting them.
    pre_well = _take(well_c, c["wellness_teams"], approved, skipped, "well")
    taken = {i["tract_geo_id"] for i in pre_well}
    lib_c = library_candidates(libraries, tracts, taken, closed, weekday)
    approved, warnings = enforce_capacity(c, approved, {"well": well_c, "truck": truck_c, "lib": lib_c}, closed)

    well = _take(well_c, c["wellness_teams"], approved, skipped, "well")
    taken = {i["tract_geo_id"] for i in well}
    lib_c = library_candidates(libraries, tracts, taken, closed, weekday)
    libs = _take(lib_c, c["libraries"], approved, skipped, "lib")
    # trucks avoid tracts that already get a wellness team, unless the operator approved that stop
    truck_pool = [t for t in truck_c if t["tract_geo_id"] not in taken or t["key"] in approved]
    trucks = _take(truck_pool, c["trucks"], approved, skipped, "truck")

    items = []
    for kind, chosen in (("well", well), ("truck", trucks), ("lib", libs)):
        for rank, it in enumerate(chosen, start=1):
            items.append({**it, "rank": rank})
    return {"constraints": c, "weekday": weekday[:3].lower(), "items": items, "approved": approved,
            "skipped": sorted(skipped), "warnings": warnings}


# ---------------------------------------------------------------------------------------------- diff + summary
def diff_plans(old_items: list[dict] | None, new_items: list[dict]) -> dict:
    """{kind: {added: [{key, name}], removed: [...]}} for kinds that changed."""
    old = {i["key"]: i for i in old_items or []}
    new = {i["key"]: i for i in new_items}
    out: dict[str, dict] = {}
    for kind in KINDS:
        add = [{"key": k, "name": v["name"]} for k, v in new.items() if v["kind"] == kind and k not in old]
        rm = [{"key": k, "name": v["name"]} for k, v in old.items() if v["kind"] == kind and k not in new]
        if add or rm:
            out[kind] = {"added": add, "removed": rm}
    return out


def constraint_changes(old: dict | None, new: dict) -> list[str]:
    o, n = normalize_constraints(old), normalize_constraints(new)
    out = []
    for k in ("trucks", "wellness_teams", "libraries", "cutoff_f", "ranking"):
        if o[k] != n[k]:
            out.append(f"{k.replace('_', ' ')}: {o[k]} -> {n[k]}")
    for s in sorted(set(n["closed_sites"]) - set(o["closed_sites"])):
        out.append(f"{s}: closed")
    for s in sorted(set(o["closed_sites"]) - set(n["closed_sites"])):
        out.append(f"{s}: reopened")
    return out


def summarize(items: list[dict], tract_layer: list[dict], cutoff_f: float) -> dict:
    """Headline numbers for the strip, from the tract layer (Toolbox rows) and the plan's items.

    hot tracts = modelled tract offset >= cutoff. Reach = residents 65+ in hot tracts that the plan touches
    (a wellness team, a library catchment, or a truck stop in the tract) / residents 65+ in all hot tracts."""
    hot = [t for t in tract_layer if (_f(t, "predicted_offset_f", None) or -99) >= cutoff_f]
    touched: set[str] = set()
    for i in items:
        if i["kind"] == "lib":
            touched.update(i.get("served_tracts") or [])
        elif i.get("tract_geo_id"):
            touched.add(i["tract_geo_id"])
    s65 = lambda rows: sum(_f(t, "pop_65_plus") or 0 for t in rows)  # noqa: E731
    hot65 = s65(hot)
    all65 = s65(tract_layer)
    reached = s65([t for t in hot if t.get("tract_geo_id") in touched])
    n = 50
    top_heat = {t.get("tract_geo_id") for t in sorted(tract_layer, key=lambda t: -(_f(t, "predicted_offset_f") or 0))[:n]}
    top_vuln = {t.get("tract_geo_id") for t in sorted(tract_layer, key=lambda t: -(_f(t, "vulnerability_index") or 0))[:n]}
    return {
        "hot_tracts": len(hot),
        "residents_in_hot_tracts": int(sum(_f(t, "total_pop") or 0 for t in hot)),
        "share_of_65_plus_in_hot_tracts": round(hot65 / all65, 3) if all65 else 0.0,
        "share_of_hot_65_plus_reached": round(reached / hot65, 3) if hot65 else 0.0,
        "top50_heat_vs_vulnerability_overlap": len(top_heat & top_vuln),
        "counts": {k: sum(1 for i in items if i["kind"] == k) for k in KINDS},
    }
