"""Verify enriched SQL uses corrected data without changing the frozen model."""
import re
from pathlib import Path

import build_enriched_tools_yaml as enriched
import build_tools_yaml as legacy


def test_enriched_context_and_baseline_model_are_separate():
    tools = {d['name']: d for d in enriched.build() if d['kind'] == 'tool'}
    sql = tools['rank_priority_sites']['statement']
    assert enriched.WORK + '.site_context_enriched' in sql
    assert enriched.SOURCE + '.heat_offset_model' in sql
    assert 'nearest_cooling_event_verified' in sql
    assert 'inside_census_chicago_place' in sql
    assert tools.keys() == {d['name'] for d in legacy.build() if d['kind'] == 'tool'}


def test_equity_audit_uses_household_and_poverty_universes():
    sql = next(d['statement'] for d in enriched.build() if d.get('name') == 'equity_audit')
    assert 'SUM(no_car_households), SUM(households)' in sql
    assert 'SUM(population_below_poverty), SUM(poverty_universe_population)' in sql
    assert 'SUM(no_vehicle_rate * total_pop)' not in sql


def test_enriched_tools_are_read_only_and_generated_file_matches():
    for doc in enriched.build():
        if doc['kind'] == 'tool':
            assert not re.search(r'\b(INSERT|UPDATE|DELETE|DROP|CREATE|MERGE|TRUNCATE)\b', doc['statement'], re.I)
    assert (Path(enriched.OUT)).read_text() == enriched.render()
