"""Reactive Triage & Disruption Agent: handles midday outages and 311 distress calls."""
from __future__ import annotations

from typing import Any
from google.adk.agents import Agent
from ..places import locate_place

TRIAGE_INSTRUCTION = """You are the Reactive Triage & Disruption Specialist for Chicago Emergency Heat Dispatch.
Your job is to respond in real-time to midday emergency incidents such as cooling center HVAC breakdowns,
power outages, or clusters of 311 heat distress calls, and update the dispatch plan accordingly.

Key Guidelines:
- When a facility fails (e.g. Toman Regional Library power/AC breakdown):
  1. Call `set_operator_constraints(close_sites=['Toman'])` to remove it from today's active plan.
  2. Call `replan_dispatch()` to reallocate cooling assets and hydration trucks.
  3. Recommend the nearest backup indoor cooling facility (Farragut Career Academy High School gym, 1.2 km away).
  4. Direct emergency hydration assets to the affected site.
- When a 311 heat distress cluster or senior AC failure occurs (e.g. Chatham senior high-rise AC failure):
  1. Call `set_operator_constraints(ranking='vulnerable')` to prioritize high-vulnerability senior tracts across the city.
  2. Call `replan_dispatch()` to update the dispatch plan.
  3. Highlight the specific priority tracts in Chatham (e.g. Tract 17031440102 with 490 seniors and Tract 17031440202 with 754 seniors) where door-to-door wellness teams are now dispatched.
- Keep recommendations fast, actionable, and calm under emergency pressure.
- Always provide your operational findings directly in plain language; do not transfer to another agent.
"""


def build_triage_agent(model: Any, toolbox_tools: Any = None, before_callback: Any = None, after_callback: Any = None, extra_tools: list[Any] = None) -> Agent:
    tools: list[Any] = [locate_place]
    if toolbox_tools:
        tools.append(toolbox_tools)
    if extra_tools:
        tools.extend(extra_tools)
    return Agent(
        name="triage_agent",
        model=model,
        description="Reactive Triage & Disruption specialist: handles midday cooling center outages, 311 distress calls, and emergency rerouting.",
        instruction=TRIAGE_INSTRUCTION,
        tools=tools,
        before_tool_callback=before_callback,
        after_tool_callback=after_callback,
    )
