import type { ChatEvent, Constraints, Day, Layers, Plan } from './types';

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  me: () => j<{ user: string; agent: string }>('/api/me'),
  days: (mode?: string) => j<{ source: string; mode?: string; days: Day[]; historic?: Day[]; current?: Day[] }>(mode && mode !== 'historic' ? `/api/days?mode=${mode}` : '/api/days'),
  layers: () => j<Layers>('/api/layers'),
  plan: () => j<Plan>('/api/plan'),
  replan: (body: { constraints?: Partial<Constraints>; day?: Day }) =>
    j<Plan>('/api/plan', { method: 'POST', body: JSON.stringify(body) }),
  action: (action: 'approve' | 'unapprove' | 'approve_all' | 'skip' | 'reset', keys: string[] = [], kind?: string) =>
    j<Plan>('/api/actions', { method: 'POST', body: JSON.stringify({ action, keys, kind }) }),
  undo: () => j<Plan>('/api/undo', { method: 'POST' }),
  reset: () => j<Plan>('/api/reset', { method: 'POST' }),

  /** POST /api/chat and yield server-sent events as they arrive. */
  async *chat(message: string, sessionId: string | null): AsyncGenerator<ChatEvent> {
    const r = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    if (!r.ok || !r.body) throw new Error(`chat failed (${r.status})`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buf += dec.decode(value, { stream: true });
      let i: number;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const line = chunk.split('\n').find(l => l.startsWith('data: '));
        if (line) yield JSON.parse(line.slice(6)) as ChatEvent;
      }
      if (done) break;
    }
  },
};
