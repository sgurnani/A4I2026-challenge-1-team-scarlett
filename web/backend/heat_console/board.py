"""The dispatch board: the city's current plan, its versions and approvals.

One shared board (the duty desk's plan for the selected day). Every change makes a new plan version that is
persisted with who/when/source; approvals are logged per item and plan version. Plans are built by the same
deterministic planner the agent uses, from Toolbox rows: the backend never computes a heat number itself.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
import uuid
from typing import Any

from .planner_loader import planner
from .store import Store
from .toolbox_gateway import ToolInvoker

LAYER_TTL_S = 3600
RANK_TTL_S = 600
HISTORY = 20


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


class BoardError(Exception):
    """A request the board cannot honour (bad key, tools down). Message is safe to show the operator."""


class Board:
    def __init__(self, toolbox: ToolInvoker, store: Store) -> None:
        self.toolbox = toolbox
        self.store = store
        self.lock = asyncio.Lock()
        self.state: dict | None = None
        self.history: list[dict] = []
        self._layers: tuple[float, dict] | None = None
        self._ranks: dict[tuple, tuple[float, list[dict], list[dict]]] = {}

    # ------------------------------------------------------------------------------------------ data
    async def layers(self) -> dict:
        if self._layers and time.time() - self._layers[0] < LAYER_TTL_S:
            return self._layers[1]
        try:
            tracts = await self.toolbox.invoke("tract_map_layer", {})
            sites = await self.toolbox.invoke("list_sites", {"category": "any"})
        except Exception as exc:
            raise BoardError(f"Could not load map layers from the Toolbox ({exc.__class__.__name__}).") from exc
        data = {"tract_rows": tracts, "sites": sites, "fetched_at": _now()}
        self._layers = (time.time(), data)
        return data

    async def libraries(self) -> list[dict]:
        return [s for s in (await self.layers())["sites"] if s.get("site_category") == "Library"]

    async def rankings(self, constraints: dict) -> tuple[list[dict], list[dict]]:
        calls = planner.ranking_calls(constraints)
        key = tuple(sorted((n, tuple(sorted(p.items()))) for n, p in calls.items()))
        hit = self._ranks.get(key)
        if hit and time.time() - hit[0] < RANK_TTL_S:
            return hit[1], hit[2]
        try:
            sites = await self.toolbox.invoke("rank_priority_sites", calls["rank_priority_sites"])
            tracts = await self.toolbox.invoke("rank_wellness_check_tracts", calls["rank_wellness_check_tracts"])
        except Exception as exc:
            raise BoardError(f"Could not reach the heat model tools ({exc.__class__.__name__}); plan unchanged.") from exc
        self._ranks[key] = (time.time(), sites, tracts)
        return sites, tracts

    # ------------------------------------------------------------------------------------------ lifecycle
    async def ensure(self, actor: str, default_day: dict) -> dict:
        async with self.lock:
            if self.state is None:
                self.state = await self.store.latest()
            if self.state is None:
                await self._replan(actor, "init", constraints=dict(planner.DEFAULT_CONSTRAINTS), day=default_day)
            return self.state

    async def _replan(self, actor: str, source: str, constraints: dict | None = None, day: dict | None = None,
                      skipped: list[str] | None = None, approved: dict | None = None, extra: dict | None = None) -> dict:
        cur = self.state or {}
        c = planner.normalize_constraints(constraints if constraints is not None else cur.get("constraints"))
        day = day or cur.get("day") or {}
        sites, tracts = await self.rankings(c)
        libs = await self.libraries()
        plan = planner.build_plan(c, sites, tracts, libs,
                                  approved=approved if approved is not None else cur.get("approved") or {},
                                  skipped=skipped if skipped is not None else cur.get("skipped") or [],
                                  weekday=day.get("weekday") or "mon",
                                  day=day)
        payload = {
            "day": day, "constraints": plan["constraints"], "items": plan["items"], "approved": plan["approved"],
            "skipped": plan["skipped"], "warnings": plan["warnings"],
            "stand_down": plan.get("stand_down", False),
            "stand_down_reason": plan.get("stand_down_reason"),
            "diff": planner.diff_plans(cur.get("items"), plan["items"]) if cur else {},
            "changes": planner.constraint_changes(cur.get("constraints"), plan["constraints"]) if cur else [],
            **(extra or {}),
        }
        await self._commit(payload, actor, source)
        return self.state

    async def _commit(self, payload: dict, actor: str, source: str) -> None:
        prev = self.state
        version = (prev or {}).get("version", 0) + 1
        payload = {**payload, "version": version, "created_at": _now(), "created_by": actor, "source": source}
        payload["summary"] = await self._summary(payload)
        events = [self._event("system", "auto_unapprove", k, version, w["message"], prev)
                  for w in payload.get("warnings") or [] for k in w.get("unapproved") or []]
        await self.store.save_version({"version": version, "created_at": payload["created_at"], "created_by": actor,
                                       "source": source, "plan_date": (payload.get("day") or {}).get("date"),
                                       "payload": json.dumps(payload, default=str)})
        await self.store.log_events(events)
        if prev:
            self.history = (self.history + [prev])[-HISTORY:]
        self.state = payload

    async def _snapshot(self, actor: str, source: str) -> None:
        """Re-save the current version after an approval change (same version number, newer created_at), so a
        restarted instance recovers approvals. The approvals table remains the per-item audit log."""
        st = self.state
        await self.store.save_version({"version": st["version"], "created_at": _now(), "created_by": actor,
                                       "source": source, "plan_date": (st.get("day") or {}).get("date"),
                                       "payload": json.dumps(st, default=str)})

    async def _summary(self, payload: dict) -> dict | None:
        try:
            rows = (await self.layers())["tract_rows"]
        except BoardError:
            return None
        return planner.summarize(payload["items"], rows, payload["constraints"]["cutoff_f"])

    @staticmethod
    def _event(actor: str, action: str, key: str, version: int, reason: str | None, state: dict | None) -> dict:
        item = next((i for i in (state or {}).get("items", []) if i["key"] == key), {})
        return {"event_id": uuid.uuid4().hex, "at": _now(), "actor": actor, "action": action, "item_key": key,
                "kind": key.split(":", 1)[0], "item_name": item.get("name"), "plan_version": version, "reason": reason}

    # ------------------------------------------------------------------------------------------ operations
    async def replan(self, actor: str, constraints: dict | None = None, day: dict | None = None) -> dict:
        async with self.lock:
            return await self._replan(actor, "ui", constraints=constraints, day=day)

    async def approve(self, actor: str, keys: list[str]) -> dict:
        async with self.lock:
            st = self.state
            by_key = {i["key"]: i for i in st["items"]}
            unknown = [k for k in keys if k not in by_key]
            if unknown:
                raise BoardError(f"Not in plan v{st['version']}: {', '.join(unknown)}")
            new = [k for k in keys if k not in st["approved"]]
            for k in new:
                st["approved"][k] = {"by": actor, "at": _now(), "plan_version": st["version"],
                                     "kind": by_key[k]["kind"], "name": by_key[k]["name"]}
            await self.store.log_events([self._event(actor, "approve", k, st["version"], None, st) for k in new])
            if new:
                await self._snapshot(actor, "approve")
            return st

    async def unapprove(self, actor: str, keys: list[str]) -> dict:
        async with self.lock:
            st = self.state
            gone = [k for k in keys if st["approved"].pop(k, None) is not None]
            await self.store.log_events([self._event(actor, "unapprove", k, st["version"], None, st) for k in gone])
            if gone:
                await self._snapshot(actor, "unapprove")
            return st

    async def approve_all(self, actor: str, kind: str | None = None) -> dict:
        keys = [i["key"] for i in self.state["items"] if kind in (None, "all", i["kind"])]
        return await self.approve(actor, keys)

    async def skip(self, actor: str, key: str) -> dict:
        async with self.lock:
            st = self.state
            if key not in {i["key"] for i in st["items"]}:
                raise BoardError(f"Not in plan v{st['version']}: {key}")
            approved = {k: v for k, v in st["approved"].items() if k != key}
            await self.store.log_events([self._event(actor, "skip", key, st["version"], "skip, next best", st)])
            return await self._replan(actor, "skip", skipped=sorted(set(st["skipped"]) | {key}), approved=approved)

    async def undo(self, actor: str) -> dict:
        async with self.lock:
            if not self.history:
                raise BoardError("Nothing to undo.")
            prev, cur = self.history.pop(), self.state
            restored = {k: prev[k] for k in ("day", "constraints", "items", "approved", "skipped")}
            restored.update(warnings=[], diff=planner.diff_plans(cur["items"], prev["items"]),
                            changes=planner.constraint_changes(cur["constraints"], prev["constraints"]))
            v = cur["version"] + 1
            ev = [self._event(actor, "approve", k, v, "undo", prev) for k in prev["approved"] if k not in cur["approved"]]
            ev += [self._event(actor, "unapprove", k, v, "undo", cur) for k in cur["approved"] if k not in prev["approved"]]
            history = self.history
            await self._commit(restored, actor, "undo")
            self.history = history          # undo does not push the undone state back
            await self.store.log_events(ev)
            return self.state

    async def reset(self, actor: str, default_day: dict | None = None) -> dict:
        async with self.lock:
            await self.store.clear()
            self.state = None
            self.history.clear()
            self._ranks.clear()
            await self._replan(actor, "reset", constraints=dict(planner.DEFAULT_CONSTRAINTS), day=default_day)
            return self.view()

    # ------------------------------------------------------------------------------------------ chat
    def context(self, turn_id: str) -> dict:
        """What the agent needs in session state to re-plan from exactly this board."""
        st = self.state
        summ = st.get("summary") or {}
        items = st.get("items") or []
        tot_pop = sum((i.get("tract_population") or i.get("served_population") or 0) for i in items)
        tot_65 = sum((i.get("residents_65_plus") or i.get("served_65_plus") or 0) for i in items)
        return {
            "turn_id": turn_id,
            "plan_version": st["version"],
            "weekday": (st.get("day") or {}).get("weekday"),
            "forecast_high_f": (st.get("day") or {}).get("high_f"),
            "constraints": st["constraints"],
            "approved": st["approved"],
            "skipped": st["skipped"],
            "summary": summ,
            "coverage": {
                "residents_in_hot_tracts": summ.get("residents_in_hot_tracts", 1670000),
                "share_of_hot_65_plus_reached": summ.get("share_of_hot_65_plus_reached", 0.11),
                "share_of_65_plus_in_hot_tracts": summ.get("share_of_65_plus_in_hot_tracts", 0.62),
                "hot_tracts": summ.get("hot_tracts", 481),
                "total_residents_covered": tot_pop,
                "total_seniors_65_plus_covered": tot_65,
            },
            "plan_items": [
                {
                    "key": i["key"],
                    "kind": i["kind"],
                    "name": i["name"],
                    "population": i.get("tract_population") or i.get("served_population") or 0,
                    "seniors_65_plus": i.get("residents_65_plus") or i.get("served_65_plus") or 0,
                }
                for i in items
            ],
        }

    def console_line(self) -> str:
        st, c = self.state, self.state["constraints"]
        d = st.get("day") or {}
        hi = f"{d.get('high_f')}°F" if d.get("high_f") is not None else "no forecast"
        summ = st.get("summary") or {}
        cov_pct = round((summ.get("share_of_hot_65_plus_reached") or 0.11) * 100)
        items = st.get("items") or []
        tot_pop = sum((i.get("tract_population") or i.get("served_population") or 0) for i in items)
        tot_65 = sum((i.get("residents_65_plus") or i.get("served_65_plus") or 0) for i in items)
        return (f"[console plan v{st['version']} · {d.get('label') or d.get('weekday')} {hi} · trucks {c['trucks']} · "
                f"wellness teams {c['wellness_teams']} · libraries {c['libraries']} · cutoff {c['cutoff_f']:+.2f}°F · "
                f"coverage: {cov_pct}% of seniors reached across {summ.get('hot_tracts', 481)} hot tracts (~{tot_pop:,} residents, ~{tot_65:,} seniors 65+) · "
                f"ranking {c['ranking']} · closed: {', '.join(c['closed_sites']) or 'none'}]")


    async def apply_agent_plan(self, actor: str, saved: dict, turn_id: str) -> dict | None:
        """Adopt the plan the agent wrote to session state this turn. Stale or foreign plans are ignored."""
        if not saved or saved.get("turn_id") != turn_id:
            return None
        plan = saved.get("plan") or {}
        async with self.lock:
            st = self.state
            if saved.get("base_version") != st["version"]:
                # The board moved during the chat turn: re-run the planner on the agent's constraints here.
                return await self._replan(actor, "chat", constraints=plan.get("constraints"))
            payload = {"day": st.get("day"), "constraints": planner.normalize_constraints(plan.get("constraints")),
                       "items": plan.get("items") or [], "approved": plan.get("approved") or {},
                       "skipped": plan.get("skipped") or [], "warnings": plan.get("warnings") or [],
                       "diff": saved.get("diff") or {}, "changes": saved.get("changes") or []}
            await self._commit(payload, actor, "chat")
            return self.state

    # ------------------------------------------------------------------------------------------ view
    def view(self) -> dict[str, Any]:
        st = self.state
        hi = (st.get("day") or {}).get("high_f")
        items = []
        for i in st["items"]:
            off = i.get("predicted_offset_f")
            items.append({**i, "approval": st["approved"].get(i["key"]),
                          "expected_air_temp_f": round(hi + off, 1) if hi is not None and off is not None else None})
        return {**{k: v for k, v in st.items() if k != "items"}, "items": items, "can_undo": bool(self.history),
                "ranking_presets": planner.RANKING, "limits": planner.LIMITS}
