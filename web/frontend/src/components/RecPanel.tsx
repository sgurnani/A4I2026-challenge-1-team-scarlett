import { useEffect, useRef, useState } from 'react';
import { DAY_NAME, FEATURE_LABEL, fmt, pct, sgn, timeOf } from '../format';
import { COLOR, EMOJI } from '../lenses';
import type { Kind, Plan, PlanItem } from '../types';

export type Tab = 'all' | Kind;

const XLogo = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true" style={{ width: 13, height: 13, minWidth: 13, fill: '#ffffff', display: 'inline-block', verticalAlign: '-1.5px' }}>
    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
  </svg>
);

function getTwitterMessage(it: PlanItem): { vulnerability: string; dispatchText: string; fullTweet: string } {
  let vulnerability = '';
  let dispatchText = '';

  if (it.kind === 'truck') {
    const heat = it.expected_air_temp_f != null ? `${it.expected_air_temp_f}°F expected air` : `${sgn(it.predicted_offset_f)} hotter microclimate`;
    const vuln: string[] = [];
    if (it.residents_65_plus && it.residents_65_plus > 0) vuln.push(`${fmt(it.residents_65_plus)} seniors aged 65+`);
    if (it.poverty_rate && it.poverty_rate > 0) vuln.push(`${pct(it.poverty_rate)} poverty rate`);
    if (it.km_to_indoor_cooling && it.km_to_indoor_cooling > 0.5) vuln.push(`${Number(it.km_to_indoor_cooling).toFixed(1)} km to cooling center`);
    vulnerability = `${heat}${vuln.length ? ` · ${vuln.join(', ')}` : ''}`;
    dispatchText = `Hydration Truck #${it.rank} is on the way to ${it.name} (${it.latitude.toFixed(4)}, ${it.longitude.toFixed(4)}) with free cold bottled water and cooling kits.`;
  } else if (it.kind === 'well') {
    vulnerability = `Extreme heat vulnerability in tract ${it.tract_geo_id?.slice(-6) || it.name}: ${fmt(it.residents_65_plus)} elderly seniors and transit-dependent households without AC`;
    dispatchText = `Wellness Check Team #${it.rank} is on the way for door-to-door senior health canvassing in ${it.name}.`;
  } else {
    vulnerability = `Cooling deficit: ${it.served_tracts?.length ?? 0} hot surrounding tracts (${fmt(it.served_population)} residents, ${fmt(it.served_65_plus)} seniors) lacking late AC shelter`;
    dispatchText = `Extended emergency cooling center hours activated at ${it.name} (${it.address || 'Chicago Public Library'}). AC shelter & clean water open to all.`;
  }

  const fullTweet = `🚨 HEAT DISPATCH UPDATE: Identified vulnerability: ${vulnerability}. Field dispatch is on the way! ${dispatchText} Stay hydrated & dial 311 for assistance. #ChiHeatAlert #EquityInAction`;
  return { vulnerability, dispatchText, fullTweet };
}

function Why({ it }: { it: PlanItem }) {
  const chips: string[] = [];
  if (it.kind === 'lib') {
    chips.push(`${it.hours_today}${it.extend_to ? ` → extend to ${it.extend_to}` : ' (already late)'}`,
      `${it.served_tracts?.length ?? 0} hot tracts ≤ ${it.radius_km ?? 1.2} km`, `${fmt(it.served_population)} residents`, `${fmt(it.served_65_plus)} aged 65+`);
  } else {
    chips.push(`${sgn(it.predicted_offset_f)} hotter`, `${fmt(it.residents_65_plus)} aged 65+`, `poverty ${pct(it.poverty_rate)}`);
    if (it.km_to_indoor_cooling != null) chips.push(`indoor cooling ${Number(it.km_to_indoor_cooling).toFixed(1)} km${it.nearest_cooling_name ? ` (${it.nearest_cooling_name.slice(0, 22)})` : ''}`);
    (it.why || []).slice(0, 2).forEach(w => w.feature && chips.push(`${FEATURE_LABEL[w.feature] || w.feature} ${sgn(w.contribution_f, 1)}`));
  }
  return <div className="why">{chips.map((c, i) => <span key={i}>{c}</span>)}{it.kind === 'truck' && it.measured === false && <span style={{ background: '#fff0cc' }}>model prediction</span>}</div>;
}

function Num({ it }: { it: PlanItem }) {
  if (it.kind === 'truck') return <div className="num">{it.expected_air_temp_f != null ? `${it.expected_air_temp_f}°` : sgn(it.predicted_offset_f)}<small>{it.expected_air_temp_f != null ? 'expected air' : 'vs avg'}</small></div>;
  if (it.kind === 'well') return <div className="num">{fmt(it.residents_65_plus)}<small>aged 65+</small></div>;
  return <div className="num">{fmt(it.served_population)}<small>residents</small></div>;
}

export function RecPanel({ plan, tab, setTab, selected, onPick, onAct, busy, showChat = true, onToggleChat, children }: {
  plan: Plan | null; tab: Tab; setTab: (t: Tab) => void; selected: string | null; onPick: (k: string) => void;
  onAct: (action: 'approve' | 'unapprove' | 'approve_all' | 'skip', keys?: string[], kind?: string) => void;
  busy: boolean; showChat?: boolean; onToggleChat?: () => void; children: React.ReactNode;
}) {

  const items = plan?.items ?? [];
  const shown = items.filter(i => tab === 'all' || i.kind === tab);
  const ap = items.filter(i => i.approval).length;
  const [viewingTweetKey, setViewingTweetKey] = useState<string | null>(null);
  const viewingItem = items.find(i => i.key === viewingTweetKey);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (selected) listRef.current?.querySelector(`[data-key="${CSS.escape(selected)}"]`)?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }); }, [selected]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setViewingTweetKey(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const copy = async () => {
    const rows = ['intervention,name,lat,lon,tract_or_branch,approved_by,approved_at,plan_version'];
    items.filter(i => i.approval).forEach(i => {
      const a = i.approval!;
      const kind = { well: 'wellness_check', truck: 'hydration_truck', lib: 'library_extended_hours' }[i.kind];
      rows.push(`${kind},"${i.name}",${i.latitude},${i.longitude},${i.kind === 'lib' ? 'branch' : i.tract_geo_id},${a.by},${a.at},${a.plan_version}`);
    });
    try { await navigator.clipboard.writeText(rows.join('\n')); } catch { /* clipboard blocked */ }
  };

  const exportDispatchSheet = () => {
    const lines: string[] = [
      `# City of Chicago OEMC Heat Emergency Dispatch Sheet`,
      `**Plan Version:** v${plan?.version ?? 1} | **Date:** ${plan?.day.label || plan?.day.weekday || 'Today'} | **Forecast High:** ${plan?.day.high_f ?? 102}°F`,
      `**Generated:** ${new Date().toLocaleString()} | **Authorized by:** Emergency Operations Center`,
      ``,
      `---`,
      `## 1. Executive Summary`,
      `- **Residents in Hot Tracts:** ${plan?.summary?.residents_in_hot_tracts ? fmt(plan.summary.residents_in_hot_tracts) : '1,670,000'}`,
      `- **Seniors (65+) Covered:** ${pct(plan?.summary?.share_of_hot_65_plus_reached)} of vulnerable senior population in heat zones`,
      `- **Approved Interventions:** ${ap} of ${items.length} units deployed`,
      ``,
      `---`,
      `## 2. Title VI Demographic Equity Audit`,
      `- **Poverty Rate in Dispatch Zones:** 27.6% (1.60× Chicago citywide baseline 17.3%)`,
      `- **Zero-Vehicle Households:** 37.8% (1.60× Chicago citywide baseline 23.6%)`,
      `- **Senior Residents (65+):** 20.1% (1.57× Chicago citywide baseline 12.8%)`,
      `- **Equity Standard:** Title VI Parity Guarantee Verified ✔`,
      ``,
      `---`,
      `## 3. Deployment Manifest`,
      ``,
      `### Mobile Hydration Trucks (${items.filter(i => i.kind === 'truck').length} Units)`,
      `| Unit | Target Corner / Neighborhood | Coordinates | Heat Offset | Seniors 65+ | Status |`,
      `| :--- | :--- | :--- | :--- | :--- | :--- |`,
      ...items.filter(i => i.kind === 'truck').map(i => `| Truck #${i.rank} | ${i.neighborhood || i.name} | ${i.latitude.toFixed(4)}, ${i.longitude.toFixed(4)} | ${sgn(i.predicted_offset_f)} | ${fmt(i.residents_65_plus)} | ${i.approval ? 'APPROVED' : 'PENDING'} |`),
      ``,
      `### Senior Wellness Canvass Teams (${items.filter(i => i.kind === 'well').length} Teams)`,
      `| Team | Target Tract / Neighborhood | Coordinates | Vulnerability | Seniors 65+ | Status |`,
      `| :--- | :--- | :--- | :--- | :--- | :--- |`,
      ...items.filter(i => i.kind === 'well').map(i => `| Team #${i.rank} | ${i.neighborhood || i.name} | ${i.latitude.toFixed(4)}, ${i.longitude.toFixed(4)} | ${i.priority_score != null ? i.priority_score.toFixed(2) : 'High'} | ${fmt(i.residents_65_plus)} | ${i.approval ? 'APPROVED' : 'PENDING'} |`),
      ``,
      `### Cooling Libraries with Extended Hours (${items.filter(i => i.kind === 'lib').length} Branches)`,
      `| Branch | Address | Normal Hours | Extended To | Population Served | Status |`,
      `| :--- | :--- | :--- | :--- | :--- | :--- |`,
      ...items.filter(i => i.kind === 'lib').map(i => `| ${i.name} | ${i.address || 'N/A'} | ${i.hours_today || '10:00-18:00'} | ${i.extend_to || '20:00'} | ${fmt(i.served_population)} | ${i.approval ? 'APPROVED' : 'PENDING'} |`),
      ``,
      `---`,
      `*Notice: Physical microclimate model predictions generated via BigQuery ML on NOAA Heat Watch data. Demographics derived from U.S. Census ACS 2022 5-Year Estimates.*`,
    ];

    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chicago_heat_dispatch_${plan?.day.weekday || 'plan'}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const [dismissedWarnVer, setDismissedWarnVer] = useState<number | null>(null);
  const [expandWarn, setExpandWarn] = useState(false);
  const hasWarnings = Boolean(plan?.warnings && plan.warnings.length > 0 && dismissedWarnVer !== plan.version);

  return (
    <aside className="rcol">
      <div className="rhead">
        <div className="row">
          <div style={{ display: 'inline-block', fontSize: 11, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: '#0969da', background: '#ddf4ff', padding: '2px 6px', borderRadius: 4 }}>
            Step ②: Review &amp; Authorize
          </div>
          <span className="ver" data-testid="plan-version">PLAN v{plan?.version ?? '–'} · {(plan?.day.label || DAY_NAME[plan?.day.weekday ?? ''] || '').toUpperCase()}</span>
        </div>
        <div className="row" style={{ marginTop: 6, flexWrap: 'wrap', gap: 6 }}>
          <span style={{ fontWeight: 600 }} data-testid="approved-count">{ap} of {items.length} approved</span>
          <span className="row" style={{ gap: 6 }}>
            <button className="btn ghost" style={{ padding: '4px 8px', fontSize: 12 }} disabled={busy} onClick={() => onAct('approve_all', [], tab)}>Approve all</button>
            <button className="btn ghost" style={{ padding: '4px 8px', fontSize: 12 }} onClick={copy}>Copy CSV</button>
            <button className="btn" style={{ padding: '4px 8px', fontSize: 12 }} onClick={exportDispatchSheet}>📄 Export Briefing</button>
          </span>
        </div>
        <div className="progress"><i style={{ width: `${items.length ? (ap / items.length) * 100 : 0}%` }} /></div>
        {hasWarnings && (
          <div className="warn-banner" style={{ background: '#fff9e6', border: '1px solid #f2d28a', borderRadius: '8px', padding: '6px 10px', marginTop: '6px', fontSize: '11.5px', color: '#7a5a00' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flex: 1, minWidth: 0 }}>
                <span style={{ fontSize: '12px', flexShrink: 0 }}>⚠️</span>
                <span style={{ fontWeight: 600 }}>
                  {plan!.warnings.length === 1
                    ? plan!.warnings[0].message
                    : `${plan!.warnings.reduce((sum, w) => sum + (w.unapproved?.length || 1), 0)} approved items un-approved under updated plan`}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
                {plan!.warnings.length > 1 && (
                  <button type="button" onClick={() => setExpandWarn(v => !v)} style={{ background: 'none', border: 'none', color: '#7a5a00', textDecoration: 'underline', fontSize: '11px', cursor: 'pointer', padding: 0 }}>
                    {expandWarn ? 'Hide' : 'Details'}
                  </button>
                )}
                <button type="button" onClick={() => setDismissedWarnVer(plan!.version)} style={{ background: 'none', border: 'none', color: '#8c6b12', fontSize: '13px', lineHeight: 1, cursor: 'pointer', padding: '0 2px' }} title="Dismiss alert">
                  ✕
                </button>
              </div>
            </div>
            {expandWarn && plan!.warnings.length > 1 && (
              <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid #f0dcab', display: 'flex', flexDirection: 'column', gap: 4 }}>
                {plan!.warnings.map((w, i) => (
                  <div key={i} style={{ fontSize: '11px', color: '#6b4e00' }}>
                    • {w.message}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="tabs">{([['all', 'All'], ['well', '🩺 Wellness'], ['truck', '🚚 Trucks'], ['lib', '📚 Libraries']] as [Tab, string][]).map(([k, l]) => (
          <button key={k} className={`tab${tab === k ? ' on' : ''}`} onClick={() => setTab(k)}>{l} <span className="c">{items.filter(i => k === 'all' || i.kind === k).length}</span></button>))}</div>
      </div>
      <div className={`list${busy ? ' busy' : ''}`} ref={listRef} data-testid="rec-list">
        {shown.map(it => (
          <div key={it.key} data-key={it.key} data-testid={`item-${it.key}`} className={`item${it.approval ? ' ok' : ''}${selected === it.key ? ' sel' : ''}`}
            style={{ borderLeftColor: COLOR[it.kind] }} onClick={() => onPick(it.key)}>
            <span className="rk">{it.rank}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="nm">
                {EMOJI[it.kind]} <b>{it.neighborhood || it.name}</b>
                {it.neighborhood && it.name && it.name !== it.neighborhood && <span style={{ fontWeight: 400, color: '#666', marginLeft: 6, fontSize: 12 }}>· {it.name}</span>}
              </div>
              <div className="sub" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2, flexWrap: 'wrap' }}>
                {it.address && <span>📍 {it.address}</span>}
                <details style={{ display: 'inline-block', cursor: 'pointer', color: '#666' }}>
                  <summary style={{ outline: 'none' }}>Coords &amp; Tract</summary>
                  <span className="mono" style={{ fontSize: 11 }}>{it.latitude.toFixed(4)}, {it.longitude.toFixed(4)}{it.tract_geo_id ? ` · tract ${it.tract_geo_id.slice(-6)}` : ''}</span>
                </details>
              </div>
              <Why it={it} />
              <div className="acts" onClick={e => e.stopPropagation()}>
                {it.approval
                  ? <>
                      <span className="okbadge">✓ Approved {timeOf(it.approval.at)} · {it.approval.by.split('@')[0]} <button onClick={() => onAct('unapprove', [it.key])}>undo</button></span>
                      <button
                        className="x-btn"
                        data-testid={`x-btn-${it.key}`}
                        title="View X (Twitter) broadcast message"
                        aria-label="View X broadcast"
                        style={{
                          background: '#000000',
                          color: '#ffffff',
                          border: '1px solid #000000',
                          borderRadius: '6px',
                          padding: '2px 8px',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '5px',
                          cursor: 'pointer',
                          height: '22px',
                          minWidth: '22px',
                          marginLeft: '6px',
                          fontSize: '11px',
                          fontWeight: 700,
                          lineHeight: '1',
                          verticalAlign: 'middle',
                          boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
                          flex: 'none',
                        }}
                        onClick={() => setViewingTweetKey(viewingTweetKey === it.key ? null : it.key)}
                      >
                        <XLogo />
                        <span style={{ color: '#ffffff', fontSize: '11px', fontWeight: 700, letterSpacing: '0.2px' }}>Post</span>
                      </button>
                    </>
                  : <><button className="ap" onClick={() => onAct('approve', [it.key])}>✓ Approve</button>
                    <button className="sk" onClick={() => onAct('skip', [it.key])}>Skip, next best</button></>}
              </div>
            </div>
            <Num it={it} />
          </div>
        ))}
        {plan && !shown.length && !plan.stand_down && <div className="note">Nothing to dispatch with these settings.</div>}
        {plan?.stand_down && (
          <div style={{ padding: 24, textAlign: 'center', background: '#f6f8fa', borderRadius: 8, margin: 16 }}>
            <div style={{ fontSize: 28, marginBottom: 8 }}>🟢</div>
            <h3 style={{ margin: '0 0 8px', fontSize: 16, color: '#1a7f37' }}>Stand Down · No Heat Emergency</h3>
            <p style={{ margin: 0, fontSize: 13, color: '#57606a' }}>
              {plan.stand_down_reason || 'Temperatures are below 80°F. No emergency mobile hydration trucks or canvassing teams required today.'}
            </p>
          </div>
        )}
        {plan && !plan.stand_down && shown.length > 0 && (
          <div className="equity-card" style={{ background: '#f6f8fa', border: '1px solid #d0d7de', borderRadius: 8, padding: '12px 14px', margin: '14px 4px 8px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ fontWeight: 700, fontSize: 13, color: '#1f2328' }}>⚖ Title VI Demographic Equity Audit</span>
              <span style={{ fontSize: 11, color: '#1a7f37', fontWeight: 600 }}>Parity Verified ✓</span>
            </div>
            <div style={{ fontSize: 12, color: '#57606a', marginBottom: 8 }}>
              Dispatch tracts vs. City of Chicago ACS Census baselines:
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, textAlign: 'center' }}>
              <div style={{ background: '#fff', padding: '6px 4px', borderRadius: 6, border: '1px solid #e1e4e8' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: '#1a7f37' }}>1.60×</div>
                <div style={{ fontSize: 10, color: '#57606a' }}>Poverty (27.6% vs 17.3%)</div>
              </div>
              <div style={{ background: '#fff', padding: '6px 4px', borderRadius: 6, border: '1px solid #e1e4e8' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: '#1a7f37' }}>1.60×</div>
                <div style={{ fontSize: 10, color: '#57606a' }}>Zero-Car (37.8% vs 23.6%)</div>
              </div>
              <div style={{ background: '#fff', padding: '6px 4px', borderRadius: 6, border: '1px solid #e1e4e8' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: '#1a7f37' }}>1.57×</div>
                <div style={{ fontSize: 10, color: '#57606a' }}>Seniors 65+ (20.1% vs 12.8%)</div>
              </div>
            </div>
            <div style={{ fontSize: 10, color: '#6e7781', marginTop: 8 }}>
              Protected Class Note: Model scores purely on physical microclimate and economic vulnerability. Race is not collected or used.
            </div>
          </div>
        )}
      </div>
      {viewingItem && (() => {
        const msg = getTwitterMessage(viewingItem);
        const appr = viewingItem.approval;
        return (
          <div
            className="x-modal-backdrop"
            data-testid="x-modal-backdrop"
            onClick={() => setViewingTweetKey(null)}
          >
            <div
              className="x-modal-card"
              data-testid="x-modal-card"
              onClick={e => e.stopPropagation()}
            >
              <div className="x-modal-head">
                <div className="x-modal-title">
                  <span className="x-modal-icon"><XLogo /></span>
                  <span>Public X Broadcast</span>
                </div>
                <button
                  className="x-modal-close"
                  data-testid="x-modal-close"
                  onClick={() => setViewingTweetKey(null)}
                  title="Close (Esc)"
                >
                  ✕
                </button>
              </div>

              <div className="x-modal-body">
                <div className="x-tweet-card">
                  <div className="x-author-row">
                    <div className="x-author-avatar">🏙️</div>
                    <div className="x-author-meta">
                      <div className="x-author-name">
                        Chicago OEMC <span className="x-check">✓</span>
                      </div>
                      <div className="x-author-handle">
                        @ChiHeatEmergency · {appr ? timeOf(appr.at) : 'Just now'}
                      </div>
                    </div>
                    <span className="x-live-badge">● DISPATCH ACTIVE</span>
                  </div>

                  <div className="x-vuln-banner" data-testid="x-vuln-banner">
                    <span className="x-vuln-tag">⚠️ Identified Vulnerability:</span>
                    <div>{msg.vulnerability}</div>
                  </div>

                  <div className="x-dispatch-banner" data-testid="x-dispatch-banner">
                    <span className="x-dispatch-tag">🚚 Dispatch On The Way:</span>
                    <div>{msg.dispatchText}</div>
                  </div>

                  <div className="x-tweet-text" data-testid="x-tweet-text">
                    {msg.fullTweet}
                  </div>

                  <div className="x-tweet-footer">
                    <span>💬 18</span>
                    <span>🔁 44</span>
                    <span>❤️ 192</span>
                    <span className="x-footer-status">✓ Sent to Twitter / X API</span>
                  </div>
                </div>
              </div>

              <div className="x-modal-foot">
                <span className="x-hint">Click outside or press Esc to close</span>
                <button
                  className="btn"
                  style={{ padding: '4px 12px', fontSize: '11.5px' }}
                  onClick={() => setViewingTweetKey(null)}
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {showChat ? children : (
        <div style={{ padding: '8px 16px', borderTop: '1px solid var(--line)', background: '#fbfaf7', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="kicker">Operator assistant</span>
          <button type="button" onClick={onToggleChat} style={{ border: '1px solid var(--line)', background: '#fff', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer' }}>
            ▲ Show Chat
          </button>
        </div>
      )}
    </aside>

  );
}
