import { geoMercator, geoPath } from 'd3-geo';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { fmt, pct, sgn } from '../format';
import { rewind } from '../geo';
import { COLOR, EMOJI, SITE_LAYERS, assignments, lensFill, lensStats, siteLayer, type LensKey, type SiteLayer } from '../lenses';
import type { Layers, Plan, PlanItem, Site } from '../types';
import { Legend } from './Legend';

const W = 1000, H = 1000;
interface VB { x: number; y: number; w: number; h: number }
interface Tip { x: number; y: number; body: ReactNode }

function SiteShape({ k, x, y, s }: { k: SiteLayer; x: number; y: number; s: number }) {
  const c = SITE_LAYERS.find(l => l.k === k)!.color;
  const st = { stroke: '#fff', strokeWidth: s * 0.28 };
  switch (k) {
    case 'lib': return <rect x={x - s} y={y - s} width={2 * s} height={2 * s} rx={s * 0.4} fill={c} {...st} />;
    case 'csc': return <path d={`M${x} ${y - s * 1.3}L${x + s * 1.3} ${y}L${x} ${y + s * 1.3}L${x - s * 1.3} ${y}Z`} fill={c} {...st} />;
    case 'park': return <path d={`M${x} ${y - s * 1.2}L${x + s * 1.2} ${y + s}L${x - s * 1.2} ${y + s}Z`} fill={c} {...st} />;
    case 'college': return <rect x={x - s * 1.1} y={y - s * 1.1} width={2.2 * s} height={2.2 * s} rx={s * 0.3} fill={c} {...st} />;
    case 'spray': return <circle cx={x} cy={y} r={s * 0.8} fill={c} {...st} />;
    case 'cps': return <rect x={x - s} y={y - s} width={2 * s} height={2 * s} fill="#fff" stroke={c} strokeWidth={s * 0.35} />;
    default: return <circle cx={x} cy={y} r={k === 'police' ? s * 1.15 : s} fill={c} {...st} />;
  }
}

export interface ViewToggles {
  showLeft: boolean;
  showRight: boolean;
  isFocus: boolean;
  onToggleLeft: () => void;
  onToggleRight: () => void;
  onToggleFocus: () => void;
}

export function MapView({ layers, plan, lens, siteLayers, selected, onPick, viewToggles }: {
  layers: Layers | null; plan: Plan | null; lens: LensKey; siteLayers: Record<SiteLayer, boolean>;
  selected: string | null; onPick: (key: string) => void;
  viewToggles?: ViewToggles;
}) {

  const svgRef = useRef<SVGSVGElement>(null);
  const [vb, setVb] = useState<VB>({ x: 0, y: 0, w: W, h: H });
  const [tip, setTip] = useState<Tip | null>(null);
  const drag = useRef<{ x: number; y: number; vb: VB; moved: boolean } | null>(null);

  const feats = useMemo(() => (layers?.tracts.features ?? []).map(rewind), [layers]);
  const proj = useMemo(() => {
    const p = geoMercator();
    if (feats.length) p.fitExtent([[10, 10], [W - 10, H - 10]], { type: 'FeatureCollection', features: feats } as never);
    return p;
  }, [feats]);
  const paths = useMemo(() => { const g = geoPath(proj); return feats.map(f => g(f as never) || ''); }, [feats, proj]);
  const stats = useMemo(() => lensStats(feats.map(f => f.properties)), [feats]);
  const assigned = useMemo(() => assignments(plan), [plan]);
  const cutoff = plan?.constraints.cutoff_f ?? 0;
  const high = plan?.day.high_f ?? null;
  const closed = new Set(plan?.constraints.closed_sites ?? []);
  const k = vb.w / W;                      // keep markers a constant on-screen size while zooming
  const xy = (lon: number, lat: number) => proj([lon, lat]) ?? [0, 0];
  const kmPx = (lat: number, lon: number, km: number) => Math.abs(xy(lon, lat)[1] - xy(lon, lat + km / 111)[1]);

  // wheel zoom (non-passive so the page does not scroll)
  useEffect(() => {
    const svg = svgRef.current; if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect();
      setVb(v => {
        const s = Math.max(v.w / r.width, v.h / r.height);
        const mx = v.x + (e.clientX - r.left) * s - (r.width * s - v.w) / 2, my = v.y + (e.clientY - r.top) * s - (r.height * s - v.h) / 2;
        const nw = Math.min(W * 1.2, Math.max(60, v.w * Math.exp(e.deltaY * 0.0015))); const kk = nw / v.w;
        return { x: mx - (mx - v.x) * kk, y: my - (my - v.y) * kk, w: v.w * kk, h: v.h * kk };
      });
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, []);

  const zoom = (f: number) => setVb(v => ({ x: v.x + v.w * (1 - f) / 2, y: v.y + v.h * (1 - f) / 2, w: v.w * f, h: v.h * f }));
  const show = (e: React.MouseEvent, body: ReactNode) => setTip({ x: e.clientX, y: e.clientY, body });
  const hide = () => setTip(null);
  const pick = (key: string) => { if (!drag.current?.moved) onPick(key); };

  const tractOf = new Map<string, PlanItem>();
  plan?.items.forEach(i => { if (i.kind === 'lib') i.served_tracts?.forEach(t => tractOf.set(t, i)); else if (i.tract_geo_id) tractOf.set(i.tract_geo_id, i); });
  const sel = plan?.items.find(i => i.key === selected);

  return (
    <div className="mapwrap">
      <svg ref={svgRef} viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`} preserveAspectRatio="xMidYMid meet" data-testid="map"
        onPointerDown={e => { drag.current = { x: e.clientX, y: e.clientY, vb, moved: false }; }}
        onPointerMove={e => {
          const d = drag.current; const svg = svgRef.current; if (!d || !svg || e.buttons !== 1) return;
          const r = svg.getBoundingClientRect(); const s = Math.max(d.vb.w / r.width, d.vb.h / r.height);
          if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 4) d.moved = true;
          setVb({ ...d.vb, x: d.vb.x - (e.clientX - d.x) * s, y: d.vb.y - (e.clientY - d.y) * s });
        }}
        onPointerUp={() => setTimeout(() => { drag.current = null; }, 0)}>
        <defs>
          <pattern id="hh" width={6 * k} height={6 * k} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width={6 * k} height={6 * k} fill="#f6d9c4" /><line x1="0" y1="0" x2="0" y2={6 * k} stroke="#d98a5f" strokeWidth={1.6 * k} />
          </pattern>
        </defs>
        <g>
          {feats.map((f, i) => {
            const t = f.properties; const a = assigned.get(t.tract_geo_id); const measured = (t.measured_readings || 0) > 0;
            return (
              <path key={t.tract_geo_id} d={paths[i]} fill={lensFill(lens, t, stats, assigned, cutoff)}
                fillOpacity={lens === 'plan' ? (a ? 0.88 : 1) : 1} stroke="#fff" strokeWidth={0.6 * k} data-testid={`tract-${t.tract_geo_id}`}
                onMouseMove={e => show(e, <>
                  <b>{t.neighborhood || 'Tract'}</b> <span className="k">· {t.tract_geo_id.slice(-6)}</span><br />
                  {high != null && <>{Math.round(high + t.predicted_offset_f)}°F street-level · </>}{sgn(t.predicted_offset_f)} vs avg (model)
                  {measured && t.measured_offset_f != null && <><br /><span className="k">measured {sgn(t.measured_offset_f)} ({t.measured_readings} readings)</span></>}<br />
                  {fmt(t.total_pop)} residents · {fmt(t.pop_65_plus)} aged 65+ · poverty {pct(t.poverty_rate)}<br />
                  <span className="k">nearest A/C site: {t.nearest_ac_name || '–'} {t.km_to_ac != null ? `(${t.km_to_ac.toFixed(1)} km)` : ''}</span>
                  {a && <><br /><b style={{ color: COLOR[a] }}>{EMOJI[a]} in today's plan</b></>}
                </>)}
                onMouseLeave={hide}
                onClick={() => { const it = tractOf.get(t.tract_geo_id); if (it) pick(it.key); }} />
            );
          })}
        </g>
        {lens !== 'plan' && <g pointerEvents="none">{feats.map((f, i) => { const a = assigned.get(f.properties.tract_geo_id); return a ? <path key={i} d={paths[i]} fill="none" stroke={COLOR[a]} strokeWidth={2.2 * k} /> : null; })}</g>}
        <g pointerEvents="none">{plan?.items.filter(i => i.kind === 'lib').map(b => {
          const [x, y] = xy(b.longitude, b.latitude);
          return <circle key={b.key} cx={x} cy={y} r={kmPx(b.latitude, b.longitude, b.radius_km || 1.2)} fill="rgba(75,79,216,.07)" stroke={COLOR.lib} strokeWidth={1 * k} strokeDasharray={`${3 * k} ${2 * k}`} />;
        })}</g>
        <g>{(layers?.sites ?? []).map((s: Site, i) => {
          const L = siteLayer(s.site_category); if (!L || !siteLayers[L]) return null;
          const [x, y] = xy(s.longitude, s.latitude); const isClosed = closed.has(s.site_name);
          return (
            <g key={i} onMouseMove={e => show(e, <>
              <b>{s.site_name}</b><br /><span className="k">{s.site_category}</span>{s.address && <><br />{s.address}</>}
              {L === 'lib' && <><br />{isClosed ? <b>closed today (operator)</b> : <span className="k">{s.service_hours || s.hours_of_operation}</span>}</>}
              {L === 'police' && <><br /><b>Open 24/7 for heat relief</b></>}
              {L === 'cps' && <><br /><span className="k">Candidate site only, not in the City's cooling plan</span></>}
              {L !== 'lib' && L !== 'police' && L !== 'cps' && s.hours_of_operation && <><br /><span className="k">{s.hours_of_operation}</span></>}
            </>)} onMouseLeave={hide} opacity={isClosed ? 0.35 : 1}>
              <SiteShape k={L} x={x} y={y} s={5.5 * k} />
            </g>
          );
        })}</g>
        <g>{plan?.items.map(it => {
          const [x, y] = xy(it.longitude, it.latitude); const ok = !!it.approval;
          return (
            <g key={it.key} transform={`translate(${x},${y})`} style={{ cursor: 'pointer' }} data-testid={`pin-${it.key}`}
              onClick={() => pick(it.key)} onMouseLeave={hide}
              onMouseMove={e => show(e, <><b>{EMOJI[it.kind]} {it.name}</b><br />
                {it.kind === 'lib' ? <>{it.hours_today}{it.extend_to ? ` → extend to ${it.extend_to}` : ''}<br />{it.served_tracts?.length} hot tracts · {fmt(it.served_population)} residents</>
                  : <>{sgn(it.predicted_offset_f)} · {fmt(it.residents_65_plus)} aged 65+{it.km_to_indoor_cooling != null ? ` · indoor cooling ${Number(it.km_to_indoor_cooling).toFixed(1)} km` : ''}</>}
                {ok && <><br /><b style={{ color: '#7fe0a3' }}>✓ approved</b></>}</>)}>
              <circle r={9 * k} fill={ok ? COLOR[it.kind] : '#fff'} stroke={COLOR[it.kind]} strokeWidth={1.8 * k} />
              <text fontSize={9.5 * k} textAnchor="middle" y={3.4 * k}>{EMOJI[it.kind]}</text>
              {ok && <circle cx={7 * k} cy={-7 * k} r={3.4 * k} fill="#13803f" stroke="#fff" strokeWidth={1 * k} />}
            </g>
          );
        })}</g>
        {sel && (() => {
          if (sel.kind === 'lib' || !sel.tract_geo_id) { const [x, y] = xy(sel.longitude, sel.latitude); return <circle cx={x} cy={y} r={13 * k} fill="none" stroke="#1e1c19" strokeWidth={2.5 * k} pointerEvents="none" />; }
          const i = feats.findIndex(f => f.properties.tract_geo_id === sel.tract_geo_id);
          return i >= 0 ? <path d={paths[i]} fill="none" stroke="#1e1c19" strokeWidth={3 * k} pointerEvents="none" /> : null;
        })()}
      </svg>
      {!feats.length && <div className="mapmsg">{layers ? 'No tract geometry returned' : 'Loading map…'}</div>}
      {viewToggles && (
        <div className="map-view-controls">
          <button type="button" onClick={viewToggles.onToggleLeft} title="Toggle Left Sidebar">
            {viewToggles.showLeft ? '◧ Hide Sidebar' : '◨ Show Sidebar'}
          </button>
          <button type="button" className={viewToggles.isFocus ? 'active' : ''} onClick={viewToggles.onToggleFocus} title="Toggle Edge-to-Edge Full Map">
            {viewToggles.isFocus ? '✕ Exit Full Map' : '⛶ Full Map'}
          </button>
          <button type="button" onClick={viewToggles.onToggleRight} title="Toggle Recommendations Panel">
            {viewToggles.showRight ? 'Hide Recs ◨' : 'Show Recs ◧'}
          </button>
        </div>
      )}
      <div className="badge-mock real">REAL · BigQuery ML · ACS · City sites · NWS</div>

      <div className="zoom"><button onClick={() => zoom(0.7)} aria-label="zoom in">+</button><button onClick={() => zoom(1.4)} aria-label="zoom out">−</button><button onClick={() => setVb({ x: 0, y: 0, w: W, h: H })} aria-label="reset view">⌂</button></div>
      <Legend lens={lens} plan={plan} stats={stats} siteLayers={siteLayers} />
      {tip && <div className="tip" style={{ display: 'block', left: Math.min(tip.x + 14, window.innerWidth - 300), top: tip.y + 14 }}>{tip.body}</div>}
    </div>
  );
}
