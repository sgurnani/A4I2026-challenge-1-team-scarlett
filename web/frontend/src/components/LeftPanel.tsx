import { useEffect, useMemo, useState } from 'react';
import { sgn } from '../format';
import { LENSES, SITE_LAYERS, siteLayer, type LensKey, type SiteLayer } from '../lenses';
import type { Constraints, Day, Layers, Plan } from '../types';

const RANK: { k: Constraints['ranking']; t: string }[] = [
  { k: 'balanced', t: 'Heat + vulnerability' }, { k: 'vulnerable', t: 'Vulnerable first' }, { k: 'heat', t: 'Heat only' },
];

export function LeftPanel({
  plan,
  days,
  lens,
  setLens,
  siteLayers,
  setSiteLayers,
  onConstraints,
  onDay,
  mode = 'historic',
  onToggleMode,
  onScenario,
  layers,
}: {
  plan: Plan | null;
  days: Day[];
  lens: LensKey;
  setLens: (l: LensKey) => void;
  siteLayers: Record<SiteLayer, boolean>;
  setSiteLayers: (s: Record<SiteLayer, boolean>) => void;
  onConstraints: (c: Partial<Constraints>) => void;
  onDay: (d: Day) => void;
  mode?: 'historic' | 'current';
  onToggleMode?: (m: 'historic' | 'current') => void;
  onScenario?: (prompt: string) => void;
  layers?: Layers | null;
}) {
  const c = plan?.constraints;
  const [draft, setDraft] = useState<Partial<Constraints>>({});
  useEffect(() => setDraft({}), [plan?.version]);
  // debounce slider drags into one re-plan
  useEffect(() => {
    if (!Object.keys(draft).length) return;
    const t = setTimeout(() => onConstraints(draft), 450);
    return () => clearTimeout(t);
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps
  const val = <K extends keyof Constraints>(k: K): Constraints[K] | undefined => (draft[k] ?? c?.[k]) as Constraints[K] | undefined;
  const [hiDraft, setHiDraft] = useState('');
  const cur = plan?.day;
  const hi = cur?.high_f;

  const siteCounts = useMemo(() => {
    const counts: Record<SiteLayer, number> = { lib: 0, police: 0, csc: 0, park: 0, senior: 0, college: 0, spray: 0, cps: 0 };
    for (const s of layers?.sites ?? []) {
      const L = siteLayer(s.site_category);
      if (L) counts[L] = (counts[L] || 0) + 1;
    }
    return counts;
  }, [layers?.sites]);

  return (
    <aside className="panel">
      <div className="kicker">City of Chicago · OEMC</div>
      <div style={{ display: 'inline-block', fontSize: 11, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: '#0969da', background: '#ddf4ff', padding: '3px 8px', borderRadius: 4, margin: '6px 0 4px' }}>
        Step ①: Pick Scenario & Test
      </div>
      <h2 style={{ fontSize: 20, margin: '4px 0 2px' }}>Incident Coordinator</h2>
      <p className="note" style={{ margin: '0 0 12px' }}>Model recommendations from BigQuery ML &amp; ACS Census. Approve or simulate midday field disruptions.</p>

      {/* Mode Toggle */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        <button
          type="button"
          className={`chip ${mode === 'historic' ? 'on' : ''}`}
          style={{ flex: 1, padding: '6px 4px', fontSize: 12, fontWeight: 600, textAlign: 'center' }}
          onClick={() => onToggleMode?.('historic')}
        >
          🔥 1995 Heatwave
        </button>
        <button
          type="button"
          className={`chip ${mode === 'current' ? 'on' : ''}`}
          style={{ flex: 1, padding: '6px 4px', fontSize: 12, fontWeight: 600, textAlign: 'center' }}
          onClick={() => onToggleMode?.('current')}
        >
          📡 Live NOAA Feed
        </button>
      </div>

      {/* 1-Click Judge Scenarios */}
      <div className="box" style={{ background: '#fef8f6', borderColor: '#f8d2c4' }}>
        <h4 style={{ color: '#b02a2a', display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: 0 }}>
          <span>One-Click Disruptions</span>
          <span className="note" style={{ color: '#b02a2a', fontWeight: 600 }}>Judge Demo</span>
        </h4>
        <p className="note" style={{ margin: '4px 0 8px' }}>Simulate real-time emergencies and watch the multi-agent system reroute assets:</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button
            type="button"
            className="btn ghost"
            style={{ textAlign: 'left', padding: '6px 8px', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}
            onClick={() => onScenario?.('Half the trucks are down due to fuel shortage. Replan dispatch with 5 trucks.')}
          >
            <span>⚠️</span> <span><b>Half Trucks Down</b> (10 &rarr; 5 trucks)</span>
          </button>
          <button
            type="button"
            className="btn ghost"
            style={{ textAlign: 'left', padding: '6px 8px', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}
            onClick={() => onScenario?.('311 Alert: Toman Regional Library has lost power and HVAC. Close Toman, activate nearest cooling center, and reroute trucks.')}
          >
            <span>🚨</span> <span><b>Toman Library Outage</b> (HVAC Down)</span>
          </button>
          <button
            type="button"
            className="btn ghost"
            style={{ textAlign: 'left', padding: '6px 8px', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}
            onClick={() => onScenario?.('311 Emergency: Chatham senior high-rise AC failure. Prioritize wellness canvass teams to Chatham.')}
          >
            <span>🚨</span> <span><b>Chatham Senior AC Failure</b> (311 Call)</span>
          </button>
        </div>
      </div>

      {/* Day Selector */}
      <div className="box">
        <h4>Forecast Day <span className="note">{hi != null ? <>{hi}°F {hi >= 80 ? <b style={{ color: '#c23b2b' }}>· Emergency</b> : '· Stand Down'}</> : 'no forecast'}</span></h4>
        <div className="chips">{days.map(d => (
          <button key={d.date || d.weekday} className={`chip${cur?.date === d.date ? ' on' : ''}`} onClick={() => onDay(d)} title={d.short_forecast || ''}>
            {d.label || d.weekday} {d.high_f != null && <b>{d.high_f}°</b>}
          </button>))}</div>
        <form className="dayhi" onSubmit={e => { e.preventDefault(); const v = parseFloat(hiDraft); if (cur && Number.isFinite(v)) { onDay({ ...cur, high_f: v }); setHiDraft(''); } }}>
          <span className="note">Override high</span><input aria-label="scenario high" inputMode="decimal" placeholder={hi != null ? String(hi) : '°F'} value={hiDraft} onChange={e => setHiDraft(e.target.value)} />
          <button className="btn ghost" style={{ padding: '3px 8px' }}>Set</button>
        </form>
      </div>

      {/* Resources & Ranking Goal */}
      <details className="box" open>
        <summary style={{ fontWeight: 600, cursor: 'pointer' }}>Resources &amp; Ranking Goal</summary>
        <div className="res" style={{ marginTop: 8 }}>
          {([['trucks', '🚚', 'trucks'], ['wellness_teams', '🩺', 'wellness teams'], ['libraries', '📚', 'libraries']] as const).map(([k, e, label]) => (
            <ResRow key={k} emoji={e} label={label} max={plan?.limits?.[k] ?? 25} value={(val(k) as number) ?? 0} onChange={v => setDraft({ ...draft, [k]: v })} />
          ))}
        </div>
        <div className="kicker" style={{ margin: '10px 0 4px' }}>Rank Priority By</div>
        <div className="seg2" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>{RANK.map(r => (
          <button key={r.k} className={val('ranking') === r.k ? 'on' : ''} onClick={() => onConstraints({ ranking: r.k })}>{r.t}</button>))}</div>
        <div className="note" style={{ marginTop: 6 }}>Mobile hydration trucks · Senior canvass teams · Extended library cooling</div>
      </details>

      {/* Collapsible Model Rules & Cutoff */}
      <details className="box">
        <summary style={{ fontWeight: 600, cursor: 'pointer' }}>Model Cutoff &amp; Closed Sites</summary>
        <div className="slider" style={{ marginTop: 8 }}><div className="row"><span>Hot: modelled offset at least</span><b>{sgn((val('cutoff_f') as number) ?? 0, 2)}</b></div>
          <input type="range" aria-label="hot cutoff" min={-1} max={2.5} step={0.25} value={(val('cutoff_f') as number) ?? 0} onChange={e => setDraft({ ...draft, cutoff_f: +e.target.value })} /></div>
        <div className="note" style={{ margin: '-2px 0 6px' }}>°F vs city average (BigQuery ML physical microclimate offset model)</div>
        {!!c?.closed_sites.length && <>
          <div className="kicker" style={{ margin: '10px 0 2px' }}>Closed today</div>
          <div className="closed">{c.closed_sites.map(s => (
            <button key={s} title="Reopen" onClick={() => onConstraints({ closed_sites: c.closed_sites.filter(x => x !== s) })}>{s} ×</button>))}</div>
        </>}
      </details>

      {/* Collapsible Map Lenses */}
      <details className="box">
        <summary style={{ fontWeight: 600, cursor: 'pointer' }}>Map Lenses &amp; Overlays</summary>
        <div className="lens" style={{ marginTop: 8 }}>{LENSES.map(l => (
          <button key={l.k} className={lens === l.k ? 'on' : ''} onClick={() => setLens(l.k)}>
            <span className="sw" style={{ background: l.sw }} /><span>{l.t}<small>{l.d}</small></span>
          </button>))}</div>
      </details>

      {/* Collapsible City Facilities */}
      <details className="box">
        <summary style={{ fontWeight: 600, cursor: 'pointer' }}>Fixed Cooling Centers (City Assets)</summary>
        <div style={{ fontSize: 11, color: '#666', margin: '4px 0 8px', lineHeight: 1.35 }}>
          Existing indoor A/C shelters &amp; splash pads. Toggle these on the map to spot cooling deserts that need mobile hydration trucks or senior canvassing.
        </div>
        <div className="layers" style={{ marginTop: 6 }}>
          {SITE_LAYERS.map(l => (
            <label key={l.k} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '2px 0' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <input type="checkbox" checked={siteLayers[l.k]} onChange={e => setSiteLayers({ ...siteLayers, [l.k]: e.target.checked })} />
                <span className="ico" style={{ background: l.color, borderRadius: 3, flexShrink: 0 }} />
                <span>{l.n}</span>
              </span>
              {siteCounts[l.k] > 0 && <small style={{ color: '#888', fontWeight: 600 }}>({siteCounts[l.k]})</small>}
            </label>
          ))}
          <span className="note" style={{ marginTop: 6, lineHeight: 1.35 }}>
            *CPS high schools &amp; City Colleges can be designated as emergency mass cooling shelters.
          </span>
        </div>
      </details>
    </aside>
  );
}

function ResRow({ emoji, label, max, value, onChange }: { emoji: string; label: string; max: number; value: number; onChange: (v: number) => void }) {
  return <>
    <span>{emoji}</span>
    <input type="range" aria-label={label} min={0} max={max} value={value} onChange={e => onChange(+e.target.value)} />
    <b>{value}</b>
  </>;
}

