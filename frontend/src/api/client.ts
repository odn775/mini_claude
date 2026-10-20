/** 后端调用层：所有 HTTP 与 SSE 解析集中在这里，组件不直接碰 fetch。 */

import type {
  ConfigOut,
  KbStatus,
  SessionCreated,
  SessionDetail,
  SessionSummary,
  StreamEvent,
} from './types'

const BASE = '/api/v1'

async function detailOf(resp: Response): Promise<string> {
  try {
    const body = await resp.json()
    return body?.detail ?? `HTTP ${resp.status}`
  } catch {
    return `HTTP ${resp.status}`
  }
}

async function asJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(await detailOf(resp))
  return (await resp.json()) as T
}

export const api = {
  getConfig: () => fetch(`${BASE}/config`).then(asJson<ConfigOut>),

  /* ── 会话 ── */
  listSessions: () => fetch(`${BASE}/sessions`).then(asJson<SessionSummary[]>),
  createSession: () =>
    fetch(`${BASE}/sessions`, { method: 'POST' }).then(asJson<SessionCreated>),
  getSession: (id: string) => fetch(`${BASE}/sessions/${id}`).then(asJson<SessionDetail>),
  deleteSession: (id: string) => fetch(`${BASE}/sessions/${id}`, { method: 'DELETE' }),

  /* ── 知识库 ── */
  kbStatus: () => fetch(`${BASE}/kb/status`).then(asJson<KbStatus>),
  kbRebuild: async () => {
    const resp = await fetch(`${BASE}/kb/rebuild`, { method: 'POST' })
    if (!resp.ok) throw new Error(await detailOf(resp))
    return resp.json()
  },

  /** 发一条消息并逐条消费 SSE 事件。 */
  streamMessages: async (
    sessionId: string,
    payload: { message: string; allow_bash: boolean },
    onEvent: (event: StreamEvent) => void,
  ): Promise<void> => {
    const resp = await fetch(`${BASE}/sessions/${sessionId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!resp.ok || !resp.body) throw new Error(await detailOf(resp))

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        const line = block.split('\n').find((l) => l.startsWith('data:'))
        if (!line) continue
        try {
          onEvent(JSON.parse(line.slice(5).trim()) as StreamEvent)
        } catch {
          /* 忽略无法解析的帧 */
        }
      }
    }
  },
}
