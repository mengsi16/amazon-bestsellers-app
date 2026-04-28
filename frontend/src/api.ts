export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface Task {
  id: string
  url: string
  browse_node_id: string
  agent_id: string | null
  model: string | null
  status: TaskStatus
  created_at: string
  updated_at: string
  workspace_path: string
  error: string | null
}

export interface Reports {
  summary: string | null
  marketplace: string | null
  reviews: string | null
  aplus: string | null
  fine_grained: string | null
}

 export type ReportKey = keyof Reports

export interface ReportsResponse {
  task_id: string
  workspace_path: string
  reports: Reports
  phases: Phases
}

export interface Phases {
  crawl: boolean
  chunk: boolean
  analyze: boolean
  summary: boolean
  qa: boolean
}

export const STAGE_KEYS = ['crawl', 'chunk', 'analyze', 'summary', 'qa'] as const
export type StageKey = typeof STAGE_KEYS[number]

export interface StageInfo {
  key: StageKey
  label: string
}

// A single structured item in the live stream.
// `kind` determines how the frontend renders it.
export type StreamItemKind =
  | 'assistant_text'
  | 'thinking'
  | 'tool_call'
  | 'system_note'
  | 'final_result'

export interface StreamItemMeta {
  tool_name?: string
  status?: 'starting' | 'running' | 'done' | 'error' | 'killed' | 'stopped' | 'completed' | string
  input_summary?: string
  result_summary?: string
  is_subagent?: boolean
  is_error?: boolean
  subagent_activities?: Array<{ description: string; duration_ms?: number; tool_uses?: number }>
  last_activity?: string
  last_duration_ms?: number
  duration_ms?: number
  total_cost_usd?: number
  num_turns?: number
}

export interface StreamItem {
  id: string
  v: number
  kind: StreamItemKind
  role: 'assistant' | 'tool' | 'system'
  content: string
  final?: boolean
  meta?: StreamItemMeta
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface HistoryResponse {
  task_id: string
  stream_items: StreamItem[]
  stream_order: string[]
  chat_messages: ChatMessage[]
}

const BASE = '/api'

export const api = {
  async createTask(url: string, model?: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, model: model || null }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Failed to create task')
    }
    return res.json()
  },

  async listTasks(): Promise<Task[]> {
    const res = await fetch(`${BASE}/tasks`)
    if (!res.ok) throw new Error('Failed to list tasks')
    return res.json()
  },

  async getTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}`)
    if (!res.ok) throw new Error('Task not found')
    return res.json()
  },

  async getReports(id: string): Promise<ReportsResponse> {
    const res = await fetch(`${BASE}/tasks/${id}/reports`)
    if (!res.ok) throw new Error('Failed to get reports')
    return res.json()
  },

  async resumeTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}/resume`, { method: 'POST' })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to resume task')
    }
    return res.json()
  },

  async refreshTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}/refresh`, { method: 'POST' })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to refresh task')
    }
    return res.json()
  },

  async reanalyzeTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}/reanalyze`, { method: 'POST' })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to reanalyze task')
    }
    return res.json()
  },

  async stopTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}/stop`, { method: 'POST' })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to stop task')
    }
    return res.json()
  },

  async deleteTask(id: string): Promise<void> {
    await fetch(`${BASE}/tasks/${id}`, { method: 'DELETE' })
  },

  getReportDownloadUrl(taskId: string, dim: ReportKey): string {
    return `${BASE}/tasks/${taskId}/download/${dim}`
  },

  async getHistory(taskId: string): Promise<HistoryResponse> {
    const res = await fetch(`${BASE}/tasks/${taskId}/history`)
    if (!res.ok) throw new Error('Failed to load history')
    return res.json()
  },
}

export type ProgressEvent =
  | { event: 'log'; data: { line: string; index: number } }
  | { event: 'phases'; data: Phases }
  | { event: 'status'; data: { status: TaskStatus; task_id: string; error: string | null } }
  | { event: 'stream_item'; data: StreamItem }
  | { event: 'stage_catalog'; data: StageInfo[] }
  | { event: 'done'; data: { status: TaskStatus } }
  | { event: 'error'; data: { message: string } }

export function openProgressSSE(
  taskId: string,
  onEvent: (e: ProgressEvent) => void,
  onDone: () => void
): EventSource {
  const es = new EventSource(`${BASE}/tasks/${taskId}/progress`)

  const handle = (eventName: string) => {
    es.addEventListener(eventName, (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        onEvent({ event: eventName, data } as ProgressEvent)
        if (eventName === 'done') {
          es.close()
          onDone()
        }
      } catch {}
    })
  }

  handle('log')
  handle('phases')
  handle('status')
  handle('stream_item')
  handle('stage_catalog')
  handle('done')
  handle('error')

  return es
}

export async function* streamChat(
  taskId: string,
  message: string
): AsyncGenerator<string> {
  const res = await fetch(`${BASE}/tasks/${taskId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })
  if (!res.ok || !res.body) throw new Error('Chat request failed')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const payload = JSON.parse(line.slice(6))
          if (payload.text) yield payload.text
          if (payload.done) return
          if (payload.error) throw new Error(payload.error)
        } catch {}
      }
    }
  }
}
