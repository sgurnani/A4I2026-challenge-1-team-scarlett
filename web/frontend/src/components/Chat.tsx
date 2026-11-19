import { useEffect, useRef, useState } from 'react';
import { marked } from 'marked';
import { EMOJI } from '../lenses';
import type { Kind, Plan } from '../types';

marked.use({ gfm: true, breaks: true });

export interface Msg { who: 'u' | 'a'; text: string; plan?: Plan; pending?: string; error?: boolean; qr?: string[] }

const TRIAGE_EVENTS = [
  { label: '🚨 Chatham Senior AC Outage', prompt: 'Midday Emergency Alert (311 Incident #311-HEAT-401): 14 calls report central AC failure at senior housing near 79th & Halsted in Chatham. Recommend immediate asset rerouting and emergency indoor cooling backup.' },
  { label: '🚨 Toman Library HVAC Failure', prompt: 'Midday Emergency Alert (311 Incident #311-HEAT-402): Toman Library cooling center in North Lawndale just suffered a power outage with 25 seniors on site. Where should we redirect them, which nearby high school cooling gym can we activate, and how should we redirect the hydration truck?' },
  { label: '🚨 Transit Heat Distress', prompt: 'Midday Emergency Alert (311 Incident #311-HEAT-403): Multiple 311 calls reporting heat exhaustion at unshaded transit corridor Pulaski & Madison in East Garfield Park. Recommend immediate dispatch rerouting.' },
];

export function DiffView({ plan, onSend, onApproveDiff }: {
  plan: Plan;
  onSend?: (t: string) => void;
  onApproveDiff?: (keys: string[], names?: string[]) => void;
}) {
  const lines: { color: string; text: string }[] = [];
  const diffAdded = (['truck', 'well', 'lib'] as Kind[]).flatMap(k => plan.diff?.[k]?.added || []);
  const diffKeys = diffAdded.map(x => x.key);
  const diffNames = diffAdded.map(x => x.name);
  const unapprovedKeys = diffKeys.filter(k => !plan.approved?.[k]);

  for (const k of ['truck', 'well', 'lib'] as Kind[]) {
    const d = plan.diff?.[k]; if (!d) continue;
    const list = (a: { name: string }[]) => a.slice(0, 5).map(x => x.name).join(', ') + (a.length > 5 ? ` +${a.length - 5} more` : '');
    if (d.removed.length) lines.push({ color: '#b3261e', text: `− ${EMOJI[k]} ${list(d.removed)}` });
    if (d.added.length) lines.push({ color: '#13803f', text: `+ ${EMOJI[k]} ${list(d.added)}` });
  }
  return (
    <div className="diff" data-testid="plan-diff" style={{ background: '#f5f7f9', padding: '8px 10px', borderRadius: '8px', border: '1px solid #d0d7de', marginTop: '6px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
        <span style={{ fontSize: '10.5px', fontWeight: 700, color: '#0969da', textTransform: 'uppercase', letterSpacing: '0.5px' }}>⚡ A2UI · Agent Dispatch Card</span>
        <span style={{ fontSize: '11px', color: '#57609a' }}>v{plan.version}</span>
      </div>
      <div className="note">Re-planned → <b>v{plan.version}</b>{plan.changes?.length ? ` · ${plan.changes.join(' · ')}` : ''}</div>
      {plan.warnings && plan.warnings.length > 0 && (
        plan.warnings.length === 1 ? (
          <div style={{ color: '#7a5a00', fontWeight: 500, fontSize: '11.5px', marginTop: 3 }}>⚠︎ {plan.warnings[0].message}</div>
        ) : (
          <details style={{ color: '#7a5a00', fontSize: '11.5px', marginTop: 3, cursor: 'pointer' }}>
            <summary style={{ fontWeight: 600 }}>⚠︎ {plan.warnings.reduce((sum, w) => sum + (w.unapproved?.length || 1), 0)} approved items un-approved under updated plan</summary>
            <div style={{ paddingLeft: 8, marginTop: 2 }}>
              {plan.warnings.map((w, i) => <div key={`w${i}`}>• {w.message}</div>)}
            </div>
          </details>
        )
      )}
      {lines.length ? lines.map((l, i) => <div key={i} style={{ color: l.color, fontWeight: 500 }}>{l.text}</div>) : <div className="note">No sites changed.</div>}
      {(onApproveDiff || onSend) && (
        <div style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
          {diffKeys.length === 0 ? (
            <button type="button" disabled style={{ background: '#e9ecef', color: '#6c757d', border: '1px solid #ced4da', borderRadius: '6px', padding: '4px 9px', fontSize: '11px', fontWeight: 600, cursor: 'default' }}>
              No added sites to approve
            </button>
          ) : unapprovedKeys.length === 0 ? (
            <button type="button" disabled style={{ background: '#eef8f2', color: '#13803f', border: '1px solid #7fe0a3', borderRadius: '6px', padding: '4px 9px', fontSize: '11px', fontWeight: 600 }}>
              ✓ Diff approved
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                if (onApproveDiff) onApproveDiff(diffKeys, diffNames);
                else onSend?.('approve changes');
              }}
              style={{ background: '#13803f', color: '#fff', border: 'none', borderRadius: '6px', padding: '4px 9px', fontSize: '11px', cursor: 'pointer', fontWeight: 600 }}
            >
              ✓ Approve changes ({diffKeys.length})
            </button>
          )}
          {onSend && (
            <button type="button" onClick={() => onSend('undo')} style={{ background: '#fff', color: '#57609a', border: '1px solid #d0d7de', borderRadius: '6px', padding: '4px 9px', fontSize: '11px', cursor: 'pointer' }}>
              ↺ Undo
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function Chat({ msgs, onSend, suggestions, busy, agentLabel, onToggleChat, onApproveDiff }: {
  msgs: Msg[]; onSend: (t: string) => void; suggestions: string[]; busy: boolean; agentLabel: string;
  onToggleChat?: () => void;
  onApproveDiff?: (keys: string[], names?: string[]) => void;
}) {
  const [text, setText] = useState('');
  const [showTriage, setShowTriage] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (box.current) box.current.scrollTop = 1e6; }, [msgs]);
  return (
    <div className="chat">
      <div className="hd">
        <span className="kicker">Operator assistant</span>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <button type="button" onClick={() => setShowTriage(v => !v)} style={{ border: '1px solid #e0c27b', background: '#fff9ea', color: '#7a5a00', borderRadius: '999px', padding: '2px 8px', fontSize: '10.5px', cursor: 'pointer', fontWeight: 600 }}>
            {showTriage ? 'Hide 311' : '⚡ A2UI 311 Triage'}
          </button>
          <span className="note">ADK agent · {agentLabel}</span>
          {onToggleChat && (
            <button type="button" onClick={onToggleChat} title="Hide Chat" style={{ border: '1px solid var(--line)', background: '#fff', borderRadius: '6px', padding: '1px 6px', fontSize: '10px', cursor: 'pointer' }}>
              ▼
            </button>
          )}
        </div>
      </div>

      {showTriage && (
        <div style={{ padding: '6px 12px', background: '#fff6e5', borderBottom: '1px solid #ecd499', display: 'flex', gap: '6px', overflowX: 'auto' }}>
          {TRIAGE_EVENTS.map(t => (
            <button key={t.label} disabled={busy} onClick={() => onSend(t.prompt)} style={{ whiteSpace: 'nowrap', border: '1px solid #d99b26', background: '#fff', color: '#8f4f00', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', fontWeight: 500 }}>
              {t.label}
            </button>
          ))}
        </div>
      )}
      <div className="msgs" ref={box} data-testid="chat-msgs">
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.who}${m.error ? ' err' : ''}`}>
            {m.text && (
              m.who === 'a' ? (
                <div className="chat-md" dangerouslySetInnerHTML={{ __html: marked.parse(m.text) as string }} />
              ) : (
                <div style={{ whiteSpace: 'pre-wrap' }}>{m.text}</div>
              )
            )}
            {m.pending && <div className="note">{m.pending}</div>}
            {m.plan && <DiffView plan={m.plan} onSend={onSend} onApproveDiff={onApproveDiff} />}
            {m.qr && <div className="qr">{m.qr.map(q => <button key={q} disabled={busy} onClick={() => onSend(q)}>{q}</button>)}</div>}
          </div>
        ))}
      </div>
      <div className="sugg">{suggestions.map(s => <button key={s} disabled={busy} onClick={() => onSend(s)}>{s}</button>)}</div>
      <form className="inrow" onSubmit={e => { e.preventDefault(); if (text.trim() && !busy) { onSend(text.trim()); setText(''); } }}>
        <input aria-label="message" autoComplete="off" placeholder="Tell me what changed, or ask why…" value={text} onChange={e => setText(e.target.value)} />
        <button className="btn" disabled={busy}>Send</button>
      </form>
    </div>
  );
}

