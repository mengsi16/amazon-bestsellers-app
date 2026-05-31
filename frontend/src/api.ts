export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface Task {
  id: string
  url: string
  browse_node_id: string
  model: string | null
  session_id: string | null
  status: TaskStatus
  created_at: string
  updated_at: string
  workspace_path: string
  error: string | null
  owner_id: string | null
  is_public: boolean
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
  v?: number
  kind: StreamItemKind
  role?: 'assistant' | 'tool' | 'system'
  content: string
  final?: boolean
  meta?: StreamItemMeta
  timestamp?: string
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

export type ModelFamily = 'sonnet' | 'opus'

export interface ModelConfig {
  id: string
  name: string
  model: string
  sonnet_model: string
  opus_model: string
  default_model_family: ModelFamily
  base_url: string
  has_api_key: boolean
  is_default: boolean
  created_at: string
}

export interface CreateModelConfigPayload {
  name: string
  apiKey: string
  baseUrl?: string
  sonnetModel?: string
  opusModel?: string
  defaultModelFamily?: ModelFamily
  isDefault?: boolean
  model?: string
}

export interface CreateTaskOptions {
  model?: string
  modelFamily?: ModelFamily
}

const BASE = '/api'

// ── JWT Token 管理 ──────────────────────────────────────────────────────────

function getToken(): string | null {
  return localStorage.getItem('token')
}

function setToken(token: string) {
  localStorage.setItem('token', token)
}

function clearToken() {
  localStorage.removeItem('token')
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export interface AuthResponse {
  user_id: string
  username: string
  token: string
}

export interface UserInfo {
  user_id: string
  username: string
  created_at: string
}

export const api = {
  // ── 认证接口 ────────────────────────────────────────────────────────────
  async register(username: string, password: string): Promise<AuthResponse> {
    const res = await fetch(`${BASE}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || '注册失败')
    }
    const data: AuthResponse = await res.json()
    setToken(data.token)
    return data
  },

  async login(username: string, password: string): Promise<AuthResponse> {
    const res = await fetch(`${BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!res.ok) {
      let err: Record<string, unknown> = {}
      try { err = await res.json() } catch {}
      throw new Error(err.detail || '用户名或密码错误')
    }
    const data: AuthResponse = await res.json()
    setToken(data.token)
    return data
  },

  async me(): Promise<UserInfo> {
    const res = await fetch(`${BASE}/auth/me`, { headers: authHeaders() })
    if (!res.ok) throw new Error('未登录')
    return res.json()
  },

  logout() {
    clearToken()
  },

  isLoggedIn(): boolean {
    return getToken() !== null
  },
  async createTask(url: string, modelOrOptions?: string | CreateTaskOptions): Promise<Task> {
    const options = typeof modelOrOptions === 'string'
      ? { model: modelOrOptions }
      : modelOrOptions
    const res = await fetch(`${BASE}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        url,
        model: options?.model || null,
        model_family: options?.modelFamily || null,
      }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Failed to create task')
    }
    return res.json()
  },

  async listTasks(params?: { status?: string; keyword?: string; all?: boolean }): Promise<Task[]> {
    const qs = new URLSearchParams()
    if (params?.status) qs.set('status', params.status)
    if (params?.keyword) qs.set('keyword', params.keyword)
    if (params?.all) qs.set('all', 'true')
    const query = qs.toString() ? `?${qs.toString()}` : ''
    const res = await fetch(`${BASE}/tasks${query}`, { headers: authHeaders() })
    if (!res.ok) throw new Error('Failed to list tasks')
    return res.json()
  },

  async getTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}`, { headers: authHeaders() })
    if (!res.ok) throw new Error('Task not found')
    return res.json()
  },

  async getReports(id: string): Promise<ReportsResponse> {
    const res = await fetch(`${BASE}/tasks/${id}/reports`, { headers: authHeaders() })
    if (!res.ok) throw new Error('Failed to get reports')
    return res.json()
  },

  async resumeTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}/resume`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to resume task')
    }
    return res.json()
  },

  async refreshTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}/refresh`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to refresh task')
    }
    return res.json()
  },

  async reanalyzeTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}/reanalyze`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to reanalyze task')
    }
    return res.json()
  },

  async deleteTask(id: string): Promise<void> {
    await fetch(`${BASE}/tasks/${id}`, { method: 'DELETE', headers: authHeaders() })
  },

  async cancelTask(id: string): Promise<Task> {
    const res = await fetch(`${BASE}/tasks/${id}/cancel`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to cancel task')
    }
    return res.json()
  },

  getReportDownloadUrl(taskId: string, dim: ReportKey): string {
    const token = getToken()
    const base = `${BASE}/tasks/${taskId}/download/${dim}`
    return token ? `${base}?token=${encodeURIComponent(token)}` : base
  },

  async getHistory(taskId: string): Promise<HistoryResponse> {
    const res = await fetch(`${BASE}/tasks/${taskId}/history`, { headers: authHeaders() })
    if (!res.ok) throw new Error('Failed to load history')
    return res.json()
  },

  async listModelConfigs(): Promise<ModelConfig[]> {
    const res = await fetch(`${BASE}/model-configs`, { headers: authHeaders() })
    if (!res.ok) throw new Error('Failed to load model configs')
    return res.json()
  },

  async createModelConfig(payload: CreateModelConfigPayload): Promise<ModelConfig> {
    const res = await fetch(`${BASE}/model-configs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        name: payload.name,
        api_key: payload.apiKey,
        base_url: payload.baseUrl || null,
        sonnet_model: payload.sonnetModel || payload.model || null,
        opus_model: payload.opusModel || null,
        default_model_family: payload.defaultModelFamily || 'sonnet',
        is_default: Boolean(payload.isDefault),
        model: payload.model || null,
      }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Failed to create model config')
    }
    return res.json()
  },

  async deleteModelConfig(id: string): Promise<void> {
    const res = await fetch(`${BASE}/model-configs/${id}`, {
      method: 'DELETE',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Failed to delete model config')
    }
  },

  async updateModelConfig(id: string, payload: Partial<CreateModelConfigPayload>): Promise<ModelConfig> {
    const res = await fetch(`${BASE}/model-configs/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        name: payload.name,
        api_key: payload.apiKey,
        base_url: payload.baseUrl || null,
        sonnet_model: payload.sonnetModel || null,
        opus_model: payload.opusModel || null,
        default_model_family: payload.defaultModelFamily || undefined,
        is_default: payload.isDefault,
        model: payload.model || null,
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '更新配置失败' }))
      throw new Error(err.detail || 'Failed to update model config')
    }
    return res.json()
  },

  async setDefaultModelConfig(id: string): Promise<void> {
    const res = await fetch(`${BASE}/model-configs/${id}/default`, {
      method: 'PUT',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Failed to set default model config')
    }
  },
}

export type ProgressEvent =
  | { event: 'log'; data: { line: string; index: number } }
  | { event: 'phases'; data: Phases }
  | { event: 'status'; data: { status: TaskStatus; task_id: string; error: string | null } }
  | { event: 'stream_item'; data: StreamItem }
  | { event: 'stage_catalog'; data: StageInfo[] }
  | { event: 'refresh_changed'; data: { changed_count: number } }
  | { event: 'done'; data: { status: TaskStatus } }
  | { event: 'error'; data: { message: string } }
  | { event: 'cost_update'; data: { cost_usd: number } }

export function openProgressSSE(
  taskId: string,
  onEvent: (e: ProgressEvent) => void,
  onDone: () => void
): EventSource {
  const token = getToken()
  const url = token
    ? `${BASE}/tasks/${taskId}/progress?token=${encodeURIComponent(token)}`
    : `${BASE}/tasks/${taskId}/progress`
  const es = new EventSource(url)

  const handle = (eventName: string) => {
    es.addEventListener(eventName, (e: MessageEvent) => {
      try {
        if (!e.data || e.data === 'undefined') {
          if (eventName === 'error') {
            onEvent({ event: 'error', data: { message: '连接异常' } } as ProgressEvent)
            es.close()
            onDone()
          }
          return
        }
        const data = JSON.parse(e.data)
        onEvent({ event: eventName, data } as ProgressEvent)
        if (eventName === 'done' || eventName === 'error') {
          es.close()
          onDone()
        }
      } catch (e) { console.error(`SSE ${eventName} parse error:`, e) }
    })
  }

  handle('log')
  handle('phases')
  handle('status')
  handle('stream_item')
  handle('stage_catalog')
  handle('refresh_changed')
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
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
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
        } catch (e) { console.error('streamChat parse error:', e) }
      }
    }
  }
}
