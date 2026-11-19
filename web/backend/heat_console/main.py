"""Heat Dispatch Console API + static frontend.

  GET  /api/health                 liveness
  GET  /api/me                     operator identity (IAP header)
  GET  /api/days                   NWS planning days (or dated days with no forecast)
  GET  /api/layers                 tract GeoJSON (tract_map_layer) + sites (list_sites), cached
  GET  /api/plan                   current plan version
  POST /api/plan                   {constraints?, day?} -> re-plan (new version)
  POST /api/actions                {action: approve|unapprove|approve_all|skip, keys?, kind?}
  POST /api/undo                   restore the previous version (as a new version)
  POST /api/chat                   {message, session_id?} -> text/event-stream of {type: session|tool|text|plan|error|done}
"""
from __future__ import annotations

import inspect
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent_gateway import AgentGateway, make_agent
from .board import Board, BoardError
from .forecast import planning_days
from .identity import current_user
from .settings import Settings, get_settings
from .store import BigQueryStore, MemoryStore
from .toolbox_gateway import ToolboxGateway, ToolInvoker

log = logging.getLogger("heat_console")


class Day(BaseModel):
    date: str | None = None
    weekday: str
    label: str | None = None
    high_f: float | None = None


class PlanRequest(BaseModel):
    constraints: dict[str, Any] | None = None
    day: Day | None = None


class ActionRequest(BaseModel):
    action: Literal["approve", "unapprove", "approve_all", "skip", "reset"]
    keys: list[str] = Field(default_factory=list)
    kind: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


def create_app(settings: Settings | None = None, toolbox: ToolInvoker | None = None,
               agent: AgentGateway | None = None, store=None, days_provider=None) -> FastAPI:
    s = settings or get_settings()
    tb = toolbox or ToolboxGateway(s.toolbox_url)
    st = store or (BigQueryStore(s.project, s.dataset) if s.store == "bigquery" else MemoryStore())
    board = Board(tb, st)
    days_fn = days_provider or (lambda mode="historic": planning_days(s.nws_user_agent, mode=mode))
    agent_box: dict[str, AgentGateway | None] = {"agent": agent}
    sessions: dict[tuple[str, str], bool] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if isinstance(tb, ToolboxGateway):
            await tb.close()

    app = FastAPI(title="Heat Dispatch Console", lifespan=lifespan)
    app.state.board = board

    def user(request: Request) -> str:
        return current_user(request, s.dev_user)

    def get_agent() -> AgentGateway:
        if agent_box["agent"] is None:
            agent_box["agent"] = make_agent(s.agent_resource_name, s.project, s.location)
        return agent_box["agent"]

    async def get_default_day() -> dict | None:
        res = days_fn()
        days_res = await res if inspect.iscoroutinefunction(days_fn) or inspect.isawaitable(res) else res
        days = days_res.get("days", []) if isinstance(days_res, dict) else []
        return next((d for d in days if d.get("high_f") == 102), days[0] if days else None)

    async def ready(request: Request) -> Board:
        if board.state is None:
            default_day = await get_default_day()
            try:
                await board.ensure(user(request), default_day)
            except BoardError as exc:
                raise HTTPException(503, str(exc)) from exc
        return board

    @app.exception_handler(BoardError)
    async def board_error(_: Request, exc: BoardError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/me")
    async def me(request: Request):
        return {"user": user(request), "agent": "deployed" if s.agent_resource_name else "local"}

    @app.get("/api/days")
    async def days(mode: str = "historic"):
        try:
            res = days_fn(mode=mode)
            return await res if inspect.iscoroutinefunction(days_fn) or inspect.isawaitable(res) else res
        except TypeError:
            res = days_fn()
            return await res if inspect.iscoroutinefunction(days_fn) or inspect.isawaitable(res) else res

    @app.get("/api/layers")
    async def layers():
        try:
            data = await board.layers()
        except BoardError as exc:
            raise HTTPException(503, str(exc)) from exc
        feats = []
        for r in data["tract_rows"]:
            geom = r.get("geometry")
            if not geom:
                continue
            props = {k: v for k, v in r.items() if k != "geometry"}
            feats.append({"type": "Feature", "id": r.get("tract_geo_id"), "properties": props,
                          "geometry": json.loads(geom) if isinstance(geom, str) else geom})
        return {"tracts": {"type": "FeatureCollection", "features": feats}, "sites": data["sites"],
                "fetched_at": data["fetched_at"]}

    @app.get("/api/plan")
    async def get_plan(request: Request):
        return (await ready(request)).view()

    @app.post("/api/plan")
    async def post_plan(req: PlanRequest, request: Request):
        b = await ready(request)
        c = {**b.state["constraints"], **(req.constraints or {})} if req.constraints is not None else None
        await b.replan(user(request), constraints=c, day=req.day.model_dump() if req.day else None)
        return b.view()

    @app.post("/api/actions")
    async def actions(req: ActionRequest, request: Request):
        b, who = await ready(request), user(request)
        if req.action == "approve":
            await b.approve(who, req.keys)
        elif req.action == "unapprove":
            await b.unapprove(who, req.keys)
        elif req.action == "approve_all":
            await b.approve_all(who, req.kind)
        elif req.action == "skip":
            if len(req.keys) != 1:
                raise HTTPException(422, "skip takes exactly one key")
            await b.skip(who, req.keys[0])
        elif req.action == "reset":
            default_day = await get_default_day()
            await b.reset(who, default_day)
        return b.view()

    @app.post("/api/undo")
    async def undo(request: Request):
        b = await ready(request)
        await b.undo(user(request))
        return b.view()

    @app.post("/api/reset")
    async def reset(request: Request):
        b, who = await ready(request), user(request)
        default_day = await get_default_day()
        await b.reset(who, default_day)
        return b.view()

    @app.post("/api/chat")
    async def chat(req: ChatRequest, request: Request):
        b, who = await ready(request), user(request)

        async def events() -> AsyncIterator[str]:
            def sse(obj: dict) -> str:
                return f"data: {json.dumps(obj, default=str)}\n\n"

            try:
                ag = get_agent()
                sid = req.session_id
                if not sid or (who, sid) not in sessions:
                    sid = await ag.create_session(who, {"title": "Dispatch console"})
                    sessions[(who, sid)] = True
                    yield sse({"type": "session", "session_id": sid})
                turn = uuid.uuid4().hex[:12]
                await ag.push_state(who, sid, {"console_context": b.context(turn),
                                               "console_constraints": b.state["constraints"], "console_plan": None})
                async for blk in ag.stream(who, sid, f"{b.console_line()}\n{req.message}"):
                    if blk["kind"] == "text":
                        yield sse({"type": "text", "text": blk["text"], "author": blk.get("author")})
                    elif blk["kind"] == "tool_call":
                        yield sse({"type": "tool", "name": blk["name"]})
                state = await ag.get_state(who, sid)
                adopted = await b.apply_agent_plan(who, state.get("console_plan") or {}, turn)
                if adopted:
                    yield sse({"type": "plan", "plan": b.view()})
            except BoardError as exc:
                yield sse({"type": "error", "message": str(exc)})
            except Exception as exc:  # surface, never hang the stream
                log.exception("chat turn failed")
                yield sse({"type": "error", "message": f"The assistant failed ({exc.__class__.__name__})."})
            yield sse({"type": "done"})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- built frontend (single-page app) ----
    if s.static_dir.is_dir():
        assets = s.static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = (s.static_dir / path).resolve()
            if path and f.is_file() and s.static_dir.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(s.static_dir / "index.html")

    return app


app = create_app()
