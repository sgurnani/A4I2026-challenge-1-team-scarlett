"""Offline tests for the console backend (web/backend). Toolbox and agent are fakes; the fake agent runs the REAL
console tools (set_operator_constraints + replan_dispatch) against session state, so the backend <-> agent contract
(console_context in, console_plan out) is exercised end to end without Gemini or GCP."""
import asyncio
import json
import types

import pytest
from fastapi.testclient import TestClient

import console_fixtures as fx
from heat_console.main import create_app
from heat_console.settings import get_settings
from heat_console.store import MemoryStore
from heat_dispatch import console

DAYS = {"source": "test", "days": [
    {"date": "2026-07-13", "weekday": "mon", "label": "Monday", "high_f": 96, "short_forecast": "Hot"},
    {"date": "2026-07-14", "weekday": "tue", "label": "Tuesday", "high_f": 99, "short_forecast": "Hotter"},
]}


class FakeToolbox:
    def __init__(self):
        self.calls = []

    async def invoke(self, name, params):
        self.calls.append((name, dict(params)))
        return await fx.fake_invoke(name, params)


class FakeAgent:
    """Stands in for the ADK agent: parses one scripted intent and calls the real console tools."""

    def __init__(self, script):
        self.script = script          # message substring -> kwargs for set_operator_constraints (None = no replan)
        self.state = {}
        self.pushed = []

    async def create_session(self, user_id, state):
        self.state = dict(state)
        return "s1"

    async def push_state(self, user_id, session_id, delta):
        self.pushed.append(delta)
        self.state.update(delta)

    async def stream(self, user_id, session_id, message):
        assert message.startswith("[console plan v")
        ctx = types.SimpleNamespace(state=self.state)
        for needle, kwargs in self.script.items():
            if needle in message:
                yield {"kind": "tool_call", "name": "set_operator_constraints"}
                await console.set_operator_constraints(**kwargs, tool_context=ctx)
                yield {"kind": "tool_call", "name": "replan_dispatch"}
                out = await console.replan_dispatch(tool_context=ctx)
                yield {"kind": "text", "text": f"Re-planned: {out['planned']}"}
                return
        yield {"kind": "text", "text": "Nothing changed."}

    async def get_state(self, user_id, session_id):
        return dict(self.state)


@pytest.fixture(autouse=True)
def fake_agent_toolbox(monkeypatch):
    monkeypatch.setattr(console, "_invoke", fx.fake_invoke)


def make(script=None, headers=None):
    tb, store, agent = FakeToolbox(), MemoryStore(), FakeAgent(script or {})

    async def days():
        return DAYS

    app = create_app(get_settings(), toolbox=tb, agent=agent, store=store, days_provider=days)
    if headers is None:
        headers = {"X-Goog-Authenticated-User-Email": "accounts.google.com:duty@city.gov"}
    c = TestClient(app, headers=headers)
    return c, tb, store, agent


def chat(c, message, session_id=None):
    events = []
    with c.stream("POST", "/api/chat", json={"message": message, "session_id": session_id}) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_initial_plan_uses_defaults_toolbox_rows_and_forecast():
    c, tb, store, _ = make()
    p = c.get("/api/plan").json()
    assert p["version"] == 1 and p["source"] == "init" and p["created_by"] == "duty@city.gov"
    assert p["constraints"]["trucks"] == 10 and p["day"]["high_f"] == 96
    t = next(i for i in p["items"] if i["kind"] == "truck")
    assert t["expected_air_temp_f"] == round(96 + t["predicted_offset_f"], 1)
    assert ("rank_priority_sites", {"limit": 50, "heat_weight": 0.5, "min_offset": 0.0}) in tb.calls
    assert p["summary"]["hot_tracts"] > 0 and len(store.versions) == 1


def test_layers_are_geojson_and_cached():
    c, tb, _, _ = make()
    a = c.get("/api/layers").json()
    c.get("/api/layers")
    assert a["tracts"]["type"] == "FeatureCollection" and a["tracts"]["features"][0]["geometry"]["type"] == "Polygon"
    assert any(s["site_category"] == "CPS High School (candidate)" for s in a["sites"])
    assert sum(1 for n, _ in tb.calls if n == "tract_map_layer") == 1


def test_approve_is_logged_per_item_and_version_and_survives_restart():
    c, _, store, _ = make()
    p = c.get("/api/plan").json()
    key = p["items"][0]["key"]
    p = c.post("/api/actions", json={"action": "approve", "keys": [key]}).json()
    item = next(i for i in p["items"] if i["key"] == key)
    assert item["approval"]["by"] == "duty@city.gov" and item["approval"]["plan_version"] == 1
    ev = store.events[-1]
    assert (ev["action"], ev["item_key"], ev["plan_version"], ev["actor"]) == ("approve", key, 1, "duty@city.gov")
    assert key in asyncio.run(store.latest())["approved"]           # a restarted instance recovers it
    assert c.post("/api/actions", json={"action": "approve", "keys": ["truck:nope"]}).status_code == 409


def test_skip_makes_new_version_with_next_best():
    c, _, store, _ = make()
    p = c.get("/api/plan").json()
    first = next(i["key"] for i in p["items"] if i["kind"] == "truck")
    p2 = c.post("/api/actions", json={"action": "skip", "keys": [first]}).json()
    assert p2["version"] == 2 and p2["source"] == "skip" and first in p2["skipped"]
    assert first not in {i["key"] for i in p2["items"]}
    assert len([i for i in p2["items"] if i["kind"] == "truck"]) == 10
    assert store.events[-1]["action"] == "skip"


def test_capacity_drop_from_ui_unapproves_lowest_and_warns():
    c, _, store, _ = make()
    p = c.get("/api/plan").json()
    trucks = [i["key"] for i in p["items"] if i["kind"] == "truck"][:4]
    c.post("/api/actions", json={"action": "approve", "keys": trucks})
    p = c.post("/api/plan", json={"constraints": {"trucks": 2}}).json()
    assert set(p["approved"]) == set(trucks[:2])
    assert p["warnings"][0]["reason"] == "capacity" and set(p["warnings"][0]["unapproved"]) == set(trucks[2:])
    auto = [e for e in store.events if e["action"] == "auto_unapprove"]
    assert {e["item_key"] for e in auto} == set(trucks[2:]) and auto[0]["actor"] == "system"


def test_day_change_replans_with_new_forecast_and_undo_restores():
    c, _, _, _ = make()
    c.get("/api/plan")
    p = c.post("/api/plan", json={"day": DAYS["days"][1]}).json()
    assert p["day"]["weekday"] == "tue" and p["version"] == 2 and p["can_undo"]
    u = c.post("/api/undo").json()
    assert u["version"] == 3 and u["day"]["weekday"] == "mon" and u["source"] == "undo"


def test_chat_replan_comes_from_session_state_not_text():
    c, _, store, agent = make({"drivers called in sick": {"trucks": 7}})
    v1 = c.get("/api/plan").json()
    ev = chat(c, "3 drivers called in sick")
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "session" and "tool" in kinds and kinds[-1] == "done"
    plan = next(e["plan"] for e in ev if e["type"] == "plan")
    assert plan["version"] == 2 and plan["source"] == "chat" and plan["constraints"]["trucks"] == 7
    assert len([i for i in plan["items"] if i["kind"] == "truck"]) == 7
    removed = {x["key"] for x in plan["diff"]["truck"]["removed"]}
    assert removed == {i["key"] for i in v1["items"] if i["kind"] == "truck"} - {i["key"] for i in plan["items"]}
    assert "trucks: 10 -> 7" in plan["changes"]
    ctx = agent.pushed[0]["console_context"]
    assert ctx["plan_version"] == 1 and ctx["weekday"] == "mon" and ctx["constraints"]["trucks"] == 10


def test_chat_closing_a_library_and_question_without_replan():
    c, _, _, _ = make({"no power": {"close_sites": ["Near"]}})
    c.get("/api/plan")
    ev = chat(c, "Near library has no power")
    plan = next(e["plan"] for e in ev if e["type"] == "plan")
    assert "Near" in plan["constraints"]["closed_sites"]
    assert "lib:Near" not in {i["key"] for i in plan["items"]}
    ev2 = chat(c, "why Hood1?", session_id="s1")
    assert not any(e["type"] == "plan" for e in ev2) and ev2[0]["type"] == "text"


def test_stale_agent_plan_is_replanned_on_current_board():
    c, _, _, _ = make()
    c.get("/api/plan")
    board = c.app.state.board
    saved = {"turn_id": "t", "base_version": 0, "plan": {"constraints": {"trucks": 3}}}
    out = asyncio.run(board.apply_agent_plan("x", saved, "t"))
    assert out["version"] == 2 and len([i for i in out["items"] if i["kind"] == "truck"]) == 3
    assert asyncio.run(board.apply_agent_plan("x", saved, "other-turn")) is None


def test_identity_falls_back_to_id_token_email_then_dev_user():
    import base64
    claims = base64.urlsafe_b64encode(json.dumps({"email": "ops@city.gov"}).encode()).decode().rstrip("=")
    c, _, _, _ = make(headers={"Authorization": f"Bearer x.{claims}.y"})
    assert c.get("/api/me").json()["user"] == "ops@city.gov"
    c2, _, _, _ = make(headers={})
    assert c2.get("/api/me").json()["user"] == "local-operator"


def test_toolbox_failure_is_a_503_not_a_crash():
    class Down:
        async def invoke(self, name, params):
            raise ConnectionError("no")

    async def days():
        return DAYS
    c = TestClient(create_app(get_settings(), toolbox=Down(), agent=FakeAgent({}), store=MemoryStore(),
                              days_provider=days))
    r = c.get("/api/plan")
    assert r.status_code == 503 and ("Toolbox" in r.json()["detail"] or "heat model" in r.json()["detail"])


def test_reset_clears_store_and_restores_baseline_plan():
    c, _, store, _ = make()
    c.get("/api/plan")
    c.post("/api/actions", json={"action": "approve_all"})
    c.post("/api/plan", json={"constraints": {"trucks": 8}})
    assert len(store.versions) >= 2
    assert len(store.events) > 0
    p2 = c.get("/api/plan").json()
    assert p2["version"] == 2
    assert len(p2["approved"]) > 0

    r = c.post("/api/reset")
    assert r.status_code == 200
    res = r.json()
    assert res["version"] == 1
    assert res["approved"] == {}
    assert res["can_undo"] is False
    assert len(store.versions) == 1
    assert len(store.events) == 0

