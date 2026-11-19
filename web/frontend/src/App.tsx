import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
import { Chat, type Msg } from './components/Chat';
import { LeftPanel } from './components/LeftPanel';
import { MapView } from './components/MapView';
import { RecPanel, type Tab } from './components/RecPanel';
import { Strip } from './components/Strip';
import { pct } from './format';
import type { LensKey, SiteLayer } from './lenses';
import type { Constraints, Day, Kind, Layers, Plan } from './types';

const QR_AFTER_REPLAN = ['undo', 'approve changes', 'How many people are covered?'];

export default function App() {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [layers, setLayers] = useState<Layers | null>(null);
  const [days, setDays] = useState<Day[]>([]);
  const [agentLabel, setAgentLabel] = useState('');
  const [lens, setLens] = useState<LensKey>('plan');
  const [siteLayers, setSiteLayers] = useState<Record<SiteLayer, boolean>>({ lib: true, police: true, csc: true, park: false, senior: false, college: false, spray: false, cps: false });
  const [tab, setTab] = useState<Tab>('all');
  const [selected, setSelected] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<'historic' | 'current'>('historic');
  const [showReliability, setShowReliability] = useState(false);
  const session = useRef<string | null>(null);
  const initialGreetingSent = useRef(false);

  const say = (m: Msg) => setMsgs(ms => [...ms, m]);
  const flash = (t: string) => { setToast(t); setTimeout(() => setToast(null), 2600); };

  useEffect(() => {
    (async () => {
      try {
        const [me, d, p] = await Promise.all([api.me(), api.days('historic'), api.plan()]);
        setAgentLabel(me.agent === 'deployed' ? 'Agent Runtime' : 'local'); setDays(d.days); setPlan(p);
        const c = p.summary?.counts;
        if (!initialGreetingSent.current) {
          initialGreetingSent.current = true;
          setMsgs([{ who: 'a', text: `Morning. Here's plan v${p.version} for ${p.day.label || p.day.weekday}${p.day.high_f != null ? ` (${p.day.high_f}°F)` : ''}: `
            + `${c?.well ?? 0} wellness teams, ${c?.truck ?? 0} trucks, ${c?.lib ?? 0} libraries with extended hours, reaching ${pct(p.summary?.share_of_hot_65_plus_reached)} of residents 65+ in hot tracts. `
            + 'Approve what looks right; tell me what changed.' }]);
        }
      } catch (e) { setError(`Could not load the plan: ${(e as Error).message}`); }
      try { setLayers(await api.layers()); } catch (e) { setError(`Could not load the map: ${(e as Error).message}`); }
    })();
  }, []);

  const run = useCallback(async (f: () => Promise<Plan>, after?: (p: Plan) => void) => {
    setBusy(true);
    try { const p = await f(); setPlan(p); after?.(p); }
    catch (e) { flash((e as Error).message); }
    finally { setBusy(false); }
  }, []);

  const warnInChat = (p: Plan) => { if (p.warnings.length) say({ who: 'a', text: '', plan: p, qr: ['undo'] }); };

  const onAct = (action: 'approve' | 'unapprove' | 'approve_all' | 'skip', keys: string[] = [], kind?: string) =>
    run(() => api.action(action, keys, kind), p => {
      if (action === 'skip') say({ who: 'a', text: `Skipped that one and pulled in the next best. Plan is now v${p.version}.`, qr: ['undo'] });
      if (action === 'approve') flash('Approved');
      if (action === 'approve_all') flash('All shown recommendations approved');
    });

  const onConstraints = (c: Partial<Constraints>) =>
    run(() => api.replan({ constraints: c }), p => {
      const parts: string[] = [];
      if (c.trucks !== undefined) parts.push(`${c.trucks} trucks`);
      if (c.wellness_teams !== undefined) parts.push(`${c.wellness_teams} wellness teams`);
      if (c.libraries !== undefined) parts.push(`${c.libraries} libraries`);
      if (c.cutoff_f !== undefined) parts.push(`+${c.cutoff_f}°F cutoff`);
      if (c.ranking !== undefined) parts.push(`${c.ranking} ranking`);

      const changeDesc = parts.length ? parts.join(', ') : 'constraints';
      say({
        who: 'a',
        text: `Operator updated ${changeDesc}. Plan recomputed to v${p.version}: ${p.summary?.counts.truck ?? 0} trucks, ${p.summary?.counts.well ?? 0} canvass teams, reaching ${pct(p.summary?.share_of_hot_65_plus_reached)} of seniors in hot tracts.`,
        plan: p.warnings.length ? p : undefined,
        qr: QR_AFTER_REPLAN,
      });
    });
  const onDay = (d: Day) => run(() => api.replan({ day: d }), warnInChat);

  const onToggleMode = async (m: 'historic' | 'current') => {
    setMode(m);
    try {
      const d = await api.days(m);
      setDays(d.days);
      if (d.days.length > 0) {
        const target = m === 'historic'
          ? (d.days.find(x => x.high_f === 102) || d.days[0])
          : d.days[0];
        await onDay(target);
      }
    } catch (e) {
      flash(`Failed to load ${m} forecast: ${(e as Error).message}`);
    }
  };

  const approveDiff = (keys: string[], itemNames?: string[]) => {
    const currentItemKeys = new Set(plan?.items.map(i => i.key) || []);
    const validKeys = keys.filter(k => currentItemKeys.has(k));
    if (!validKeys.length) {
      flash('No new sites in diff to approve');
      return;
    }
    run(() => api.action('approve', validKeys), () => {
      flash(`Approved ${validKeys.length} changed site${validKeys.length > 1 ? 's' : ''}`);
      say({
        who: 'a',
        text: `✓ Approved ${validKeys.length} changed site${validKeys.length > 1 ? 's' : ''} from diff${itemNames?.length ? `: ${itemNames.join(', ')}` : ''}.`,
      });
    });
  };

  const onResetAll = () => {
    if (!window.confirm('Reset all demo state? This will clear all persisted approvals, plan versions, and database records so judges can start fresh.')) {
      return;
    }
    run(() => api.reset(), p => {
      session.current = null;
      setMsgs([{ who: 'a', text: `Morning. Here's plan v${p.version} for ${p.day.weekday} ${p.day.date || ''} (${p.day.label || ''}) (${p.day.high_f}°F): ${p.summary?.counts.well ?? 8} wellness teams, ${p.summary?.counts.truck ?? 5} trucks, ${p.summary?.counts.lib ?? 6} libraries with extended hours, reaching ${pct(p.summary?.share_of_hot_65_plus_reached)} of residents 65+ in hot tracts. Approve what looks right; tell me what changed.` }]);
      flash('Demo database reset to clean baseline plan (v1)');
    });
  };

  const send = async (raw: string) => {
    const text = raw.trim(); if (!text) return;
    say({ who: 'u', text });
    if (/^(undo|revert|go back)\b/i.test(text)) {
      return run(() => api.undo(), p => say({ who: 'a', text: `Reverted to the previous plan (now v${p.version}).` }));
    }
    if (/^(reset( all| demo)?|clear( all| database)?|fresh start)\b/i.test(text)) {
      return run(() => api.reset(), p => {
        session.current = null;
        say({ who: 'a', text: `🔄 Database reset complete! All persisted plan versions and approvals have been wiped clean. Reset to baseline Plan v${p.version} (102°F Peak Alert).` });
        flash('Database reset to clean baseline plan (v1)');
      });
    }
    if (/^approve (changes|diff)\b/i.test(text)) {
      const diffAdded = (['truck', 'well', 'lib'] as Kind[]).flatMap(k => plan?.diff?.[k]?.added || []);
      const diffKeys = diffAdded.map(x => x.key);
      const diffNames = diffAdded.map(x => x.name);
      return approveDiff(diffKeys, diffNames);
    }
    if (/^approve all\b/i.test(text)) {
      return run(() => api.action('approve_all'), p => say({ who: 'a', text: `Approved everything. ${p.items.filter(i => i.approval).length} of ${p.items.length} now approved.` }));
    }
    setBusy(true);
    const idx = { current: -1 };
    setMsgs(ms => { idx.current = ms.length; return [...ms, { who: 'a', text: '', pending: 'Thinking…' }]; });
    const patch = (f: (m: Msg) => Msg) => setMsgs(ms => ms.map((m, i) => (i === idx.current ? f(m) : m)));
    try {
      for await (const ev of api.chat(text, session.current)) {
        if (ev.type === 'session') session.current = ev.session_id;
        else if (ev.type === 'tool') patch(m => ({ ...m, pending: `Working… (${ev.name.replace(/_/g, ' ')})` }));
        else if (ev.type === 'text') patch(m => ({ ...m, text: m.text ? `${m.text}\n${ev.text}` : ev.text }));
        else if (ev.type === 'plan') { setPlan(ev.plan); patch(m => ({ ...m, plan: ev.plan, qr: QR_AFTER_REPLAN })); }
        else if (ev.type === 'error') patch(m => ({ ...m, text: ev.message, error: true }));
      }
    } catch (e) {
      patch(m => ({ ...m, text: `The assistant is unavailable (${(e as Error).message}).`, error: true }));
    } finally {
      patch(m => ({ ...m, pending: undefined }));
      setBusy(false);
    }
  };

  const [showLeft, setShowLeft] = useState(true);
  const [showRight, setShowRight] = useState(true);
  const [showChat, setShowChat] = useState(true);

  const libName = plan?.items.find(i => i.kind === 'lib')?.name;
  const suggestions = ['Half the trucks are down (fuel shortage)', '2 drivers called in sick', ...(libName ? [`${libName} library has no power`] : []),
    'Rank by vulnerability first', 'We got 3 extra wellness teams', 'How many people are covered?'];

  const gridStyle = useMemo(() => {
    if (!showLeft && !showRight) return { gridTemplateColumns: '1fr' };
    if (!showLeft) return { gridTemplateColumns: '1fr var(--right-w, 420px)' };
    if (!showRight) return { gridTemplateColumns: 'var(--left-w, 280px) 1fr' };
    return { gridTemplateColumns: 'var(--left-w, 280px) 1fr var(--right-w, 420px)' };
  }, [showLeft, showRight]);

  const viewToggles = useMemo(() => ({
    showLeft,
    showRight,
    isFocus: !showLeft && !showRight,
    onToggleLeft: () => setShowLeft(v => !v),
    onToggleRight: () => setShowRight(v => !v),
    onToggleFocus: () => {
      if (!showLeft && !showRight) {
        setShowLeft(true);
        setShowRight(true);
      } else {
        setShowLeft(false);
        setShowRight(false);
      }
    },
  }), [showLeft, showRight]);

  const hi = plan?.day.high_f;
  const isStandDown = plan?.stand_down;
  const c = plan?.constraints;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Top Command Headline Banner */}
      <header style={{
        background: isStandDown ? '#15241b' : '#1e1c19',
        color: '#f5f2ec',
        padding: '8px 16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        borderBottom: '1px solid rgba(255,255,255,0.12)',
        fontSize: '13px',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{
            background: isStandDown ? '#198754' : '#b02a2a',
            color: '#fff',
            fontWeight: 700,
            fontSize: '11px',
            padding: '3px 8px',
            borderRadius: 4,
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4
          }}>
            {isStandDown ? '🟢 Stand Down' : '🔴 Extreme Heat Emergency'}
          </span>
          <span style={{ fontWeight: 600, fontSize: '14px' }}>
            {plan?.day.label || plan?.day.weekday || 'Today'} · {hi != null ? `${hi}°F Forecast` : 'Forecast'}
            {hi != null && hi >= 95 ? ' (Peak Alert)' : ''}
          </span>
          <span style={{ color: '#c7c2b8', fontSize: '12px' }}>
            {isStandDown
              ? 'Normal weather conditions (<80°F) — No heat dispatch active'
              : `Deploy ${c?.trucks ?? 10} Hydration Trucks · ${c?.wellness_teams ?? 8} Wellness Teams · Extend ${c?.libraries ?? 6} Libraries`}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ fontSize: '11.5px', color: '#a09b91', display: 'flex', gap: 6 }}>
            <span>① Pick Scenario</span>
            <span>➔</span>
            <span>② Review Plan</span>
            <span>➔</span>
            <span>③ Authorize</span>
          </div>
          <button
            type="button"
            className="btn ghost"
            title="Clear all persisted approvals, plan versions, and database records so judges can start fresh"
            style={{ padding: '3px 9px', fontSize: '11.5px', background: 'rgba(255,255,255,0.08)', color: '#ffd280', border: '1px solid rgba(255,210,128,0.3)', cursor: 'pointer' }}
            onClick={onResetAll}
            disabled={busy}
          >
            🔄 Reset Demo / DB
          </button>
          <button
            type="button"
            className="btn ghost"
            style={{ padding: '3px 9px', fontSize: '11.5px', background: 'rgba(255,255,255,0.1)', color: '#fff', border: '1px solid rgba(255,255,255,0.2)' }}
            onClick={() => setShowReliability(true)}
          >
            ℹ Model Limitations
          </button>
        </div>
      </header>

      {/* Main 3-Column Work Area */}
      <div className="app" style={{ ...gridStyle, flex: 1, minHeight: 0 }}>
        {showLeft && (
          <LeftPanel
            plan={plan}
            days={days}
            lens={lens}
            setLens={setLens}
            siteLayers={siteLayers}
            setSiteLayers={setSiteLayers}
            onConstraints={onConstraints}
            onDay={onDay}
            mode={mode}
            onToggleMode={onToggleMode}
            onScenario={send}
            layers={layers}
          />
        )}
        <main className="center">
          <Strip plan={plan} />
          <MapView layers={layers} plan={plan} lens={lens} siteLayers={siteLayers} selected={selected} onPick={setSelected} viewToggles={viewToggles} />
        </main>
        {showRight && (
          <RecPanel plan={plan} tab={tab} setTab={setTab} selected={selected} onPick={setSelected} onAct={onAct} busy={busy} showChat={showChat} onToggleChat={() => setShowChat(v => !v)}>
            <Chat msgs={msgs} onSend={send} suggestions={suggestions} busy={busy} agentLabel={agentLabel} onToggleChat={() => setShowChat(false)} onApproveDiff={approveDiff} />
          </RecPanel>
        )}
      </div>

      {/* Model Reliability & Limitations Modal */}
      {showReliability && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.6)',
          backdropFilter: 'blur(3px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 9999,
          padding: 20
        }} onClick={() => setShowReliability(false)}>
          <div style={{
            background: '#fff',
            color: '#1e1c19',
            maxWidth: 580,
            width: '100%',
            borderRadius: 12,
            padding: 24,
            boxShadow: '0 12px 40px rgba(0,0,0,0.3)',
            lineHeight: 1.5,
          }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, borderBottom: '1px solid #e3ddd2', paddingBottom: 8 }}>
              <h3 style={{ margin: 0, fontSize: 18 }}>ℹ Model Transparency &amp; Reliability</h3>
              <button style={{ border: 0, background: 'none', fontSize: 18, cursor: 'pointer' }} onClick={() => setShowReliability(false)}>✕</button>
            </div>
            <div style={{ fontSize: 13, color: '#333', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <p><b>Purpose:</b> Operational decision-support tool for dispatchers during extreme weather, providing fast recommendations for placing hydration trucks, senior wellness teams, and extended cooling centers.</p>
              <div style={{ background: '#f8f9fa', padding: 12, borderRadius: 8, border: '1px solid #e1e4e8' }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>BigQuery ML Physical Microclimate Model (R² ≈ 0.26)</div>
                <div style={{ fontSize: 12, color: '#57606a' }}>
                  Trained on NOAA Heat Watch afternoon sensor traverses across Chicago. It models physical temperature differentials relative to the city average:
                  <ul style={{ margin: '6px 0 0 16px', padding: 0 }}>
                    <li><b>Lake Michigan proximity:</b> +1.1°F inland heat penalty as lake breeze dissipates</li>
                    <li><b>Tree Canopy (NDVI):</b> Up to -1.4°F cooling effect from dense urban greenery</li>
                    <li><b>Pre-1940 Housing Era:</b> +0.5°F thermal retention from uninsulated brick masonry</li>
                    <li><b>Impervious Surface &amp; Building Density:</b> +0.4°F solar radiation absorption</li>
                  </ul>
                </div>
              </div>
              <div style={{ background: '#eef9fd', padding: 12, borderRadius: 8, border: '1px solid #cbebf6' }}>
                <div style={{ fontWeight: 600, color: '#0969da', marginBottom: 4 }}>Title VI Demographic Equity Standard</div>
                <div style={{ fontSize: 12, color: '#2c5282' }}>
                  <b>Race and demographic factors are strictly excluded from ML model training.</b> Microclimate heat scores are combined downstream with U.S. Census ACS 2022 socioeconomic factors (seniors 65+, zero-vehicle households, poverty). Our Title VI post-dispatch audit guarantees that resources consistently reach historically underserved tracts at rates 1.5×–1.6× higher than city averages.
                </div>
              </div>
              <p style={{ fontSize: 11.5, color: '#6e7781', margin: 0 }}>
                <i>Notice:</i> The model serves as an operational prioritization signal, not a point thermometer. Always cross-reference with real-time 311 distress calls.
              </p>
            </div>
            <div style={{ marginTop: 18, textAlign: 'right' }}>
              <button className="btn" style={{ padding: '6px 16px' }} onClick={() => setShowReliability(false)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {error && <div className="toast" style={{ display: 'block', background: '#b3261e' }}>{error}</div>}
      {toast && <div className="toast" style={{ display: 'block' }}>{toast}</div>}
    </div>
  );
}


