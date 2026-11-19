"""Logistics & Facilities Agent: sites water trucks and pairs with libraries and high school gyms."""
from __future__ import annotations

from typing import Any
from google.adk.agents import Agent
from ..places import locate_place

LOGISTICS_INSTRUCTION = """You are the Logistics & Facilities Specialist for Chicago Emergency Heat Dispatch.
Your job is to site mobile hydration trucks at high-priority street intersections and identify indoor cooling facilities
(Chicago Public Libraries for hours extension, and CPS High School gymnasiums for mass emergency cooling).

Key Guidelines:
- Use `rank_priority_sites` to find the highest-priority heat/vulnerability intersections for water trucks.
- Use `nearest_cooling_facilities` with `facility_type='Library'` to find the closest CPL library branches to extend hours.
- Use `nearest_cooling_facilities` with `facility_type='High School'` to identify CPS High School gymnasiums with large-capacity indoor air conditioning.
- When recommending CPS High Schools for emergency cooling activation, prioritize validated regional facilities (e.g. Farragut Career Academy in North Lawndale, Westinghouse College Prep in Garfield Park, or Harlan Community Academy in Chatham/Roseland). Do NOT get bogged down in raw `is_indoor` database flags; explain their gym capacity and how they back up neighborhood cooling centers.
- Provide practical operational context: specify exact cross streets, parking/staging feasibility, and nearest indoor cooling distance.
- Always provide your operational findings directly in plain language; do not transfer to another agent.
"""


def build_logistics_agent(model: Any, toolbox_tools: Any = None, before_callback: Any = None, after_callback: Any = None) -> Agent:
    tools: list[Any] = [locate_place]
    if toolbox_tools:
        tools.append(toolbox_tools)
    return Agent(
        name="logistics_agent",
        model=model,
        description="Logistics specialist: answers questions about specific intersection cross-streets, truck staging feasibility, and CPS High School cooling gyms. Do NOT use for console capacity adjustments, driver changes, or plan re-ranking.",
        instruction=LOGISTICS_INSTRUCTION,
        tools=tools,
        before_tool_callback=before_callback,
        after_tool_callback=after_callback,
    )

