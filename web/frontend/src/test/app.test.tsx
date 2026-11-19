import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import type { Plan, PlanItem } from '../types';

// ---------------------------------------------------------------- fixtures
const item = (kind: PlanItem['kind'], i: number, extra: Partial<PlanItem> = {}): PlanItem => ({
  key: `${kind}:${kind === 'lib' ? `Branch${i}` : `id${i}`}`, kind, rank: i + 1, name: kind === 'lib' ? `Branch${i}` : `Hood${kind}${i}`,
  latitude: 41.8 + i * 0.01, longitude: -87.65, tract_geo_id: kind === 'lib' ? null : `1703100000${i}`, priority_score: 0.9 - i * 0.05,
  predicted_offset_f: 1.2 - i * 0.1, expected_air_temp_f: 97.2 - i * 0.1, residents_65_plus: 400, poverty_rate: 0.3,
  km_to_indoor_cooling: 1.1, measured: i % 2 === 0, why: [{ feature: 'lake_distance_band', contribution_f: 0.8 }],
  ...(kind === 'lib' ? { hours_today: '10 am-6 pm', extend_to: '10 pm', served_tracts: ['17031000001'], served_population: 5000, served_65_plus: 700, radius_km: 1.2 } : {}),
  approval: null, ...extra,
});

function makePlan(version: number, items: PlanItem[], extra: Partial<Plan> = {}): Plan {
  return {
    version, created_at: '2026-07-13T14:00:00Z', created_by: 'duty@city.gov', source: 'init',
    day: { date: '2026-07-13', weekday: 'mon', label: 'Monday', high_f: 96 },
    constraints: { trucks: 3, wellness_teams: 2, libraries: 1, closed_sites: [], cutoff_f: 0, ranking: 'balanced' },
    items, approved: Object.fromEntries(items.filter(i => i.approval).map(i => [i.key, i.approval!])),
    skipped: [], warnings: [], diff: {}, changes: [],
    summary: { hot_tracts: 120, residents_in_hot_tracts: 300000, share_of_65_plus_in_hot_tracts: 0.4, share_of_hot_65_plus_reached: 0.2, top50_heat_vs_vulnerability_overlap: 11, counts: { well: 2, truck: 3, lib: 1 } },
    can_undo: version > 1, ranking_presets: { balanced: 0.5, heat: 1, vulnerable: 0.2 }, limits: { trucks: 40, wellness_teams: 40, libraries: 20 },
    ...extra,
  };
}

const v1Items = [item('well', 0), item('well', 1), item('truck', 0), item('truck', 1), item('truck', 2), item('lib', 0)];
const LAYERS = {
  fetched_at: 'now', sites: [{ site_name: 'Branch0', site_category: 'Library', latitude: 41.8, longitude: -87.65, service_hours: 'Mon. & Wed., 10-6' }],
  tracts: { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { tract_geo_id: '17031000001', neighborhood: 'Englewood', latitude: 41.8, longitude: -87.65, predicted_offset_f: 1.1, total_pop: 3000, pop_65_plus: 400, vulnerability_index: 0.7, area_km2: 0.5 }, geometry: { type: 'Polygon', coordinates: [[[-87.65, 41.8], [-87.64, 41.8], [-87.64, 41.81], [-87.65, 41.8]]] } }] },
};

// ---------------------------------------------------------------- fake backend
type Handler = (body: any) => unknown;
let routes: Record<string, Handler>;
let calls: { path: string; body: any }[];

function sse(events: object[]): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(c) { events.forEach(e => c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`))); c.close(); },
  });
  return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
}

beforeEach(() => {
  calls = [];
  routes = {
    'GET /api/me': () => ({ user: 'duty@city.gov', agent: 'local' }),
    'GET /api/days': () => ({ source: 'test', days: [{ date: '2026-07-13', weekday: 'mon', label: 'Monday', high_f: 96 }] }),
    'GET /api/plan': () => makePlan(1, v1Items),
    'GET /api/layers': () => LAYERS,
  };
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: `${method} ${path}`, body });
    const h = routes[`${method} ${path}`];
    if (!h) return new Response(JSON.stringify({ detail: 'no route' }), { status: 404 });
    const out = h(body);
    return out instanceof Response ? out : new Response(JSON.stringify(out), { headers: { 'Content-Type': 'application/json' } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

async function boot() {
  render(<App />);
  await screen.findByTestId('item-truck:id0');
  await waitFor(() => expect(screen.getByTestId('tract-17031000001')).toBeInTheDocument());
}

// ---------------------------------------------------------------- tests
describe('Heat Dispatch Console', () => {
  it('renders the plan from the backend: cards, strip, map, greeting', async () => {
    await boot();
    expect(screen.getByTestId('plan-version')).toHaveTextContent('PLAN v1 · MONDAY');
    expect(screen.getByTestId('approved-count')).toHaveTextContent('0 of 6 approved');
    expect(screen.getByTestId('strip')).toHaveTextContent('120');
    expect(screen.getByTestId('pin-lib:Branch0')).toBeInTheDocument();
    expect(within(screen.getByTestId('item-truck:id0')).getByText('97.2°')).toBeInTheDocument();
    expect(screen.getByTestId('chat-msgs')).toHaveTextContent("Here's plan v1 for Monday (96°F)");
  });

  it('approve sends the key and shows who approved it', async () => {
    routes['POST /api/actions'] = body => {
      expect(body).toEqual({ action: 'approve', keys: ['truck:id0'] });
      return makePlan(1, v1Items.map(i => i.key === 'truck:id0' ? { ...i, approval: { by: 'duty@city.gov', at: '2026-07-13T14:05:00Z', plan_version: 1, kind: 'truck', name: i.name } } : i));
    };
    await boot();
    await userEvent.click(within(screen.getByTestId('item-truck:id0')).getByRole('button', { name: /approve/i }));
    await waitFor(() => expect(screen.getByTestId('approved-count')).toHaveTextContent('1 of 6 approved'));
    expect(within(screen.getByTestId('item-truck:id0')).getByText(/Approved/)).toHaveTextContent('duty');
  });

  it('skip replaces the card with the next best and bumps the version', async () => {
    routes['POST /api/actions'] = body => {
      expect(body).toEqual({ action: 'skip', keys: ['truck:id0'] });
      return makePlan(2, [...v1Items.filter(i => i.key !== 'truck:id0'), item('truck', 3)], { source: 'skip', skipped: ['truck:id0'] });
    };
    await boot();
    await userEvent.click(within(screen.getByTestId('item-truck:id0')).getByRole('button', { name: /skip/i }));
    await screen.findByTestId('item-truck:id3');
    expect(screen.queryByTestId('item-truck:id0')).not.toBeInTheDocument();
    expect(screen.getByTestId('plan-version')).toHaveTextContent('PLAN v2');
    expect(screen.getByTestId('chat-msgs')).toHaveTextContent('Plan is now v2');
  });

  it('chat re-plan: streams the reply, applies the plan from the backend, shows the diff and capacity warning', async () => {
    const approved = { by: 'duty@city.gov', at: '2026-07-13T14:05:00Z', plan_version: 1, kind: 'truck' as const, name: 'x' };
    const v2 = makePlan(2, [item('well', 0), item('well', 1), item('truck', 0, { approval: approved }), item('lib', 0)], {
      source: 'chat', constraints: { trucks: 1, wellness_teams: 2, libraries: 1, closed_sites: [], cutoff_f: 0, ranking: 'balanced' },
      changes: ['trucks: 3 -> 1'],
      diff: { truck: { added: [], removed: [{ key: 'truck:id1', name: 'Hoodtruck1' }, { key: 'truck:id2', name: 'Hoodtruck2' }] } },
      warnings: [{ kind: 'truck', reason: 'capacity', unapproved: ['truck:id1'], message: 'You had approved 2 hydration truck item(s) but only 1 are available. Un-approved the 1 lowest-priority: Hoodtruck1' }],
    });
    routes['POST /api/chat'] = body => {
      expect(body).toEqual({ message: '2 drivers called in sick', session_id: null });
      return sse([{ type: 'session', session_id: 's1' }, { type: 'tool', name: 'set_operator_constraints' }, { type: 'tool', name: 'replan_dispatch' },
        { type: 'text', text: 'Down to 1 truck; I dropped the two lowest-priority stops.' }, { type: 'plan', plan: v2 }, { type: 'done' }]);
    };
    await boot();
    await userEvent.type(screen.getByLabelText('message'), '2 drivers called in sick{enter}');
    const diff = await screen.findByTestId('plan-diff');
    expect(diff).toHaveTextContent('Re-planned → v2 · trucks: 3 -> 1');
    expect(diff).toHaveTextContent('− 🚚 Hoodtruck1, Hoodtruck2');
    expect(diff).toHaveTextContent('only 1 are available');
    expect(screen.getByTestId('chat-msgs')).toHaveTextContent('Down to 1 truck');
    expect(screen.queryByTestId('item-truck:id1')).not.toBeInTheDocument();
    expect(screen.getByTestId('plan-version')).toHaveTextContent('PLAN v2');
    expect(screen.getAllByText(/only 1 are available/).length).toBeGreaterThanOrEqual(2);   // warning also on the panel

    // the session id is reused on the next turn
    routes['POST /api/chat'] = body => { expect(body.session_id).toBe('s1'); return sse([{ type: 'text', text: 'ok' }, { type: 'done' }]); };
    await userEvent.type(screen.getByLabelText('message'), 'why Hoodwell0?{enter}');
    await waitFor(() => expect(calls.filter(c => c.path === 'POST /api/chat')).toHaveLength(2));
  });

  it('undo quick reply calls /api/undo instead of the agent', async () => {
    routes['POST /api/undo'] = () => makePlan(3, v1Items, { source: 'undo' });
    await boot();
    await userEvent.type(screen.getByLabelText('message'), 'undo{enter}');
    await waitFor(() => expect(screen.getByTestId('plan-version')).toHaveTextContent('PLAN v3'));
    expect(calls.some(c => c.path === 'POST /api/chat')).toBe(false);
  });

  it('resource slider re-plans with only the changed constraint (debounced)', async () => {
    routes['POST /api/plan'] = body => makePlan(2, v1Items, { constraints: { ...makePlan(1, []).constraints, ...body.constraints } });
    await boot();
    const slider = screen.getByLabelText('trucks') as HTMLInputElement;
    const { fireEvent } = await import('@testing-library/react');
    fireEvent.change(slider, { target: { value: '5' } });
    await waitFor(() => expect(calls.find(c => c.path === 'POST /api/plan')?.body).toEqual({ constraints: { trucks: 5 } }), { timeout: 2000 });
  });

  it('approve button in chat diff approves only the changed keys in the diff, not the whole plan', async () => {
    const newItem = item('truck', 3);
    const v2 = makePlan(2, [...v1Items, newItem], {
      source: 'chat',
      changes: ['trucks: 3 -> 4'],
      diff: { truck: { added: [{ key: newItem.key, name: newItem.name }], removed: [] } },
    });
    routes['POST /api/chat'] = () => sse([
      { type: 'session', session_id: 's1' },
      { type: 'text', text: 'Added 1 truck.' },
      { type: 'plan', plan: v2 },
      { type: 'done' },
    ]);
    routes['POST /api/actions'] = body => {
      expect(body).toEqual({ action: 'approve', keys: [newItem.key] });
      return {
        ...v2,
        approved: {
          [newItem.key]: { by: 'duty@city.gov', at: '2026-07-13T14:10:00Z', plan_version: 2, kind: 'truck', name: newItem.name },
        },
      };
    };

    await boot();
    await userEvent.type(screen.getByLabelText('message'), 'add 1 truck{enter}');
    const diff = await screen.findByTestId('plan-diff');
    expect(diff).toHaveTextContent('+ 🚚 Hoodtruck3');
    const approveBtn = within(diff).getByRole('button', { name: /approve changes/i });
    await userEvent.click(approveBtn);

    const actionCall = calls.find(c => c.path === 'POST /api/actions');
    expect(actionCall).toBeDefined();
    expect(actionCall?.body).toEqual({ action: 'approve', keys: [newItem.key] });
  });

  it('reset demo button clears database and restores baseline plan v1', async () => {
    routes['POST /api/reset'] = () => makePlan(1, v1Items, { source: 'reset' });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await boot();
    const resetBtn = screen.getByRole('button', { name: /reset demo/i });
    await userEvent.click(resetBtn);
    await waitFor(() => expect(calls.some(c => c.path === 'POST /api/reset')).toBe(true));
  });

  it('reset chat command calls /api/reset and posts confirmation', async () => {
    routes['POST /api/reset'] = () => makePlan(1, v1Items, { source: 'reset' });
    await boot();
    await userEvent.type(screen.getByLabelText('message'), 'reset demo{enter}');
    await waitFor(() => expect(calls.some(c => c.path === 'POST /api/reset')).toBe(true));
    expect(screen.getByTestId('chat-msgs')).toHaveTextContent('Database reset complete');
  });

  it('shows X icon on approval and clicking it displays vulnerability and dispatch on way, closes on click outside', async () => {
    const approvedItem = { ...v1Items[0], approval: { by: 'duty@city.gov', at: '2026-07-13T14:05:00Z', plan_version: 1, kind: v1Items[0].kind, name: v1Items[0].name } };
    routes['POST /api/actions'] = () => makePlan(1, [approvedItem, ...v1Items.slice(1)]);
    await boot();

    // Click Approve on the first item
    const firstItem = screen.getByTestId(`item-${v1Items[0].key}`);
    const apBtn = within(firstItem).getByText('✓ Approve');
    await userEvent.click(apBtn);

    // The X button should now populate at the end of the approval section
    const xBtn = await screen.findByTestId(`x-btn-${v1Items[0].key}`);
    expect(xBtn).toBeInTheDocument();

    // Click the X button to open the message
    await userEvent.click(xBtn);

    // The message card appears with vulnerability and dispatch on the way
    const modal = await screen.findByTestId('x-modal-card');
    expect(modal).toBeInTheDocument();
    expect(screen.getByTestId('x-vuln-banner')).toHaveTextContent(/Identified Vulnerability/i);
    expect(screen.getByTestId('x-dispatch-banner')).toHaveTextContent(/Dispatch On The Way/i);
    expect(screen.getByTestId('x-tweet-text')).toHaveTextContent(/🚨 HEAT DISPATCH UPDATE/);

    // Clicking outside (on the backdrop) closes the modal
    const backdrop = screen.getByTestId('x-modal-backdrop');
    await userEvent.click(backdrop);
    expect(screen.queryByTestId('x-modal-card')).not.toBeInTheDocument();
  });
});
