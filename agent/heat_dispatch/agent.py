"""Heat Dispatch Assistant: ADK root agent, deployed to Agent Runtime.

        root_agent
          |- MCP Toolbox toolset  (BigQuery: ML.EXPLAIN_PREDICT ranking, explanations, frequency, equity audit)
          |- locate_place / get_forecast_high / heat_risk_for_forecast   (small Python tools)
          |- get_dispatch_status / set_operator_constraints / replan_dispatch   (web console re-plans, console.py)
          `- build_dispatch_plan  (assembles the Placement Recommendation from stored tool output)
"""
from __future__ import annotations

from typing import Any

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai.types import HttpRetryOptions

from .config import get_settings
from .console import get_dispatch_status, replan_dispatch, set_operator_constraints
from .dispatch import STATE_AUDIT, STATE_PARAMS, STATE_SITES, STATE_TRACTS, build_dispatch_plan
from .forecast import get_forecast_high, heat_risk_for_forecast
from .places import in_chicago, locate_place
from .prompts import INSTRUCTION
from .results import parse_rows
from .subagents import (
    build_equity_agent,
    build_logistics_agent,
    build_outreach_agent,
    build_spatial_risk_agent,
    build_triage_agent,
)
from .toolbox import build_toolbox_toolset

S = get_settings()

# tool name -> session-state key holding its rows (read later by build_dispatch_plan)
_STASH = {"rank_priority_sites": STATE_SITES, "rank_wellness_check_tracts": STATE_TRACTS, "equity_audit": STATE_AUDIT}
_POINT_TOOLS = {"predict_site_heat", "who_lives_near", "nearest_cooling_facilities"}


def stash_tool_results(tool: Any, args: dict, tool_context: Any, tool_response: Any) -> None:
    """After a Toolbox tool returns, keep its rows in session state. The model's reply is untouched."""
    key = _STASH.get(getattr(tool, "name", ""))
    if not key:
        return None
    rows = parse_rows(tool_response)
    if rows:
        tool_context.state[key] = rows
        if tool.name == "rank_priority_sites":
            tool_context.state[STATE_PARAMS] = {k: v for k, v in (args or {}).items()
                                                if k in ("heat_weight", "limit", "min_density", "spacing_cells", "min_offset")}
    return None


def guard_point_tools(tool: Any, args: dict, tool_context: Any) -> dict | None:
    """Refuse, deterministically, to run point tools for coordinates outside Chicago (no model call, no guessing)."""
    if getattr(tool, "name", "") in _POINT_TOOLS:
        try:
            lat, lon = float(args.get("latitude")), float(args.get("longitude"))
        except (TypeError, ValueError):
            return {"status": "error", "message": "latitude and longitude are required; use locate_place first."}
        if not in_chicago(lat, lon):
            return {"status": "outside_area", "message": "That point is outside the City of Chicago. This assistant "
                    "only has a heat model for Chicago, so it cannot say anything about it."}
    return None


def _model() -> Gemini:
    return Gemini(model=S.model, retry_options=HttpRetryOptions(initial_delay=1, max_delay=4, attempts=6),
                  client_kwargs={"enterprise": True, "location": S.model_location})


toolbox = build_toolbox_toolset(S)

spatial_risk_agent = build_spatial_risk_agent(_model(), toolbox, guard_point_tools, stash_tool_results)
logistics_agent = build_logistics_agent(_model(), toolbox, guard_point_tools, stash_tool_results)
outreach_agent = build_outreach_agent(_model(), toolbox, guard_point_tools, stash_tool_results)
triage_agent = build_triage_agent(_model(), toolbox, guard_point_tools, stash_tool_results,
                                  [set_operator_constraints, replan_dispatch])
equity_agent = build_equity_agent(_model(), toolbox, guard_point_tools, stash_tool_results)
_SPECIALISTS = [spatial_risk_agent, logistics_agent, outreach_agent, triage_agent, equity_agent]

# The builders attach the guard and stash callbacks. Also: specialists hand back to the coordinator, never to each other,
# otherwise a compound request ping-pongs between agents (observed live: 4-11 transfers and no answer).
for _a in _SPECIALISTS:
    _a.disallow_transfer_to_peers = True

root_agent = Agent(
    name="heat_dispatch_agent",
    model=_model(),
    description="Chicago emergency heat incident coordinator: orchestrates 5 specialized agents (Spatial Risk, Logistics, Outreach, Triage, Equity) to generate proactive heat dispatch plans and respond to midday disruptions.",
    instruction=INSTRUCTION,
    tools=[toolbox, locate_place, get_forecast_high, heat_risk_for_forecast, build_dispatch_plan,
           get_dispatch_status, set_operator_constraints, replan_dispatch],
    sub_agents=_SPECIALISTS,
    before_tool_callback=guard_point_tools,
    after_tool_callback=stash_tool_results,
)
