/** 与后端 /api/v1 契约对应的类型（见 backend/app/schemas）。 */

export interface ConfigOut {
  model: string
  base_url: string
  default_allow_bash: boolean
}

export type SessionSource = 'web' | 'cli'

export interface SessionSummary {
  id: string
  title: string
  source: SessionSource
  model: string
  created_at: string
  updated_at: string
}

export interface SessionDetail extends SessionSummary {
  messages: { role: string; content: string }[]
}

export interface SessionCreated {
  id: string
  title: string
  source: SessionSource
  model: string
}

export interface KbStatus {
  building: boolean
  last_error: string | null
  exists: boolean
  total_chunks: number
  files: string[]
}

export interface ToolInfo {
  name: string
  description: string
  source: 'builtin' | 'mcp'
}

/** SSE 事件（见 backend/app/core/events.py）。 */
export type StreamEvent =
  | { type: 'text_delta'; content: string }
  | { type: 'tool_call'; name: string; arguments: Record<string, unknown>; call_id: string }
  | { type: 'tool_result'; name: string; call_id: string; content: string }
  | { type: 'text'; content: string }
  | { type: 'usage'; input_tokens: number; output_tokens: number; cached_tokens: number }
  | { type: 'compact'; before_tokens: number; after_tokens: number; degraded: boolean; error: string | null }
  | { type: 'notice'; message: string }
  | { type: 'error'; kind?: string; message: string }
  | { type: 'done' }

/** 界面消息模型：历史消息只有 user/assistant，流式中的一轮是 turn。 */
export type ChatRole = 'user' | 'assistant' | 'error' | 'turn'

export interface ToolRun {
  key: string
  name: string
  status: 'running' | 'done'
  args: string
  result: string
}

export interface ChatMessage {
  role: ChatRole
  text: string
  tools?: ToolRun[]
  thinking?: boolean
  statusText?: string
}
