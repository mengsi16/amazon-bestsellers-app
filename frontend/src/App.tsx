import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { FileText, RotateCcw, RefreshCcw, AlertTriangle, X, ArrowUp } from 'lucide-react'
import { api, openProgressSSE, streamChat } from './api'
import type { Task, TaskStatus, Phases, Reports, StreamItem, StageInfo, AuthResponse, UserInfo, ModelFamily } from './api'
import Sidebar from './components/Sidebar'
import ReportViewer from './components/ReportViewer'
import NewTaskDialog from './components/NewTaskDialog'
import StageRail from './components/StageRail'
import LiveStream, { type LocalMessage } from './components/LiveStream'
import ToastContainer, { useToast } from './components/Toast'
import AuthPage from './components/AuthPage'
import SettingsView from './components/SettingsView'

const EMPTY_PHASES: Phases = { crawl: false, chunk: false, analyze: false, summary: false, qa: false }

function EmptyState({ onCreate, creating }: { onCreate: (url: string) => void; creating: boolean }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')

  const submit = () => {
    const trimmed = url.trim()
    setError('')
    if (!trimmed) {
      setError('请输入 Amazon Bestsellers 类目 URL')
      return
    }
    if (!trimmed.includes('amazon.com') || !trimmed.includes('bestsellers')) {
      setError('请使用完整的 Amazon Bestsellers 类目 URL')
      return
    }
    onCreate(trimmed)
  }

  return (
    <div className="flex h-full items-center justify-center px-8">
      <div className="w-full max-w-3xl">
        <h1 className="mb-8 text-center text-2xl font-medium tracking-normal text-[var(--text-primary)]">
          分析哪个 Amazon 类目？
        </h1>
        <div className="overflow-hidden rounded-[var(--radius-xl)] border border-[var(--border-default)] bg-[var(--bg-raised)] shadow-sm">
          <textarea
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
            rows={3}
            placeholder="粘贴 Amazon Bestsellers 类目 URL"
            className="block min-h-[92px] w-full resize-none bg-transparent px-4 py-3 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-disabled)]"
          />
          <div className="flex items-center justify-between border-t border-[var(--border-default)] bg-[var(--bg-base)] px-3 py-2">
            <span className="text-xs text-[var(--text-tertiary)]">amazon-bestsellers</span>
            <button
              type="button"
              onClick={submit}
              disabled={creating}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--text-primary)] text-white transition-colors hover:bg-black disabled:opacity-40"
              title="开始分析"
            >
              <ArrowUp size={15} />
            </button>
          </div>
        </div>
        {error && <p className="mt-2 text-center text-xs text-[var(--error)]">{error}</p>}
        <p className="mt-4 text-center text-xs text-[var(--text-tertiary)]">
          默认使用设置页中的模型配置；已有中间数据会自动续跑。
        </p>
      </div>
    </div>
  )
}

export default function App() {
  const toast = useToast()
  const [user, setUser] = useState<UserInfo | null>(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [tasks, setTasks] = useState<Task[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [showDialog, setShowDialog] = useState(false)
  const [creating, setCreating] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [reanalyzing, setReanalyzing] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [showReanalyzeConfirm, setShowReanalyzeConfirm] = useState(false)
  const [showReports, setShowReports] = useState(false)
  const [view, setView] = useState<'workspace' | 'settings'>('workspace')
  // T15: 任务筛选
  const [statusFilter, setStatusFilter] = useState<TaskStatus | ''>('')
  const [keywordFilter, setKeywordFilter] = useState('')

  // Per-task live state
  const [phases, setPhases] = useState<Phases>(EMPTY_PHASES)
  const [stageCatalog, setStageCatalog] = useState<StageInfo[] | null>(null)
  const [streamItems, setStreamItems] = useState<Record<string, StreamItem>>({})
  const [streamOrder, setStreamOrder] = useState<string[]>([])
  const [localMessages, setLocalMessages] = useState<LocalMessage[]>([])
  const [reports, setReports] = useState<{ path: string; data: Reports } | null>(null)
  const [currentCost, setCurrentCost] = useState<number>(0)

  const activeTask = tasks.find((t) => t.id === activeId) ?? null
  const sseRef = useRef<EventSource | null>(null)
  const selectAbortRef = useRef<AbortController | null>(null)

  // Derived list of stream items in display order.
  const itemsArray = useMemo<StreamItem[]>(() => {
    const out: StreamItem[] = []
    for (const id of streamOrder) {
      const it = streamItems[id]
      if (it) out.push(it)
    }
    return out
  }, [streamItems, streamOrder])

  // The item the StageRail should highlight as "current activity" —
  // prefer the most recent non-final subagent tool_call, else any non-final tool_call.
  const currentItem = useMemo<StreamItem | null>(() => {
    for (let i = itemsArray.length - 1; i >= 0; i--) {
      const it = itemsArray[i]
      if (it.kind === 'tool_call' && !it.final && it.meta?.is_subagent) return it
    }
    for (let i = itemsArray.length - 1; i >= 0; i--) {
      const it = itemsArray[i]
      if (it.kind === 'tool_call' && !it.final) return it
    }
    return null
  }, [itemsArray])

  // ── Reset per-task state ─────────────────────────────────────────────────
  const resetTaskState = useCallback(() => {
    setPhases(EMPTY_PHASES)
    setStageCatalog(null)
    setStreamItems({})
    setStreamOrder([])
    setLocalMessages([])
    setReports(null)
    setShowReports(false)
    setCurrentCost(0)
  }, [])

  // ── 认证检查 ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (api.isLoggedIn()) {
      api.me()
        .then((u) => setUser(u))
        .catch((e) => { console.error('认证检查失败:', e); api.logout() })
        .finally(() => setAuthChecked(true))
    } else {
      setAuthChecked(true)
    }
  }, [])

  const handleAuth = useCallback((data: AuthResponse) => {
    setUser({ user_id: data.user_id, username: data.username, created_at: '' })
  }, [])

  const handleLogout = useCallback(() => {
    api.logout()
    setUser(null)
    setActiveId(null)
    setView('workspace')
    resetTaskState()
  }, [resetTaskState])

  // ── Task list polling ────────────────────────────────────────────────────
  const loadTasks = useCallback(async () => {
    try {
      const list = await api.listTasks({
        status: statusFilter || undefined,
        keyword: keywordFilter || undefined,
      })
      setTasks(list)
      return list
    } catch (e) {
      console.error('加载任务列表失败:', e)
      return []
    }
  }, [statusFilter, keywordFilter])

  // 初始加载 + 轮询
  useEffect(() => {
    if (user) loadTasks()
    const interval = setInterval(() => { if (user) loadTasks() }, 10_000)
    return () => clearInterval(interval)
  }, [loadTasks, user])

  // ── Handle SSE events ────────────────────────────────────────────────────
  const applySSEEvent = useCallback((event: string, data: unknown) => {
    if (event === 'phases') {
      setPhases(data as Phases)
      return
    }
    if (event === 'stage_catalog') {
      setStageCatalog(data as StageInfo[])
      return
    }
    if (event === 'stream_item') {
      const item = data as StreamItem
      setStreamItems((prev) => ({ ...prev, [item.id]: item }))
      setStreamOrder((prev) => (prev.includes(item.id) ? prev : [...prev, item.id]))
      // 从 final_result 提取成本
      if (item.kind === 'final_result' && item.meta?.total_cost_usd) {
        setCurrentCost(item.meta.total_cost_usd)
      }
      return
    }
    if (event === 'status') {
      const { status, task_id, error } = data as { status: Task['status']; task_id: string; error: string | null }
      setTasks((prev) => {
        const prevTask = prev.find((t) => t.id === task_id)
        // 任务状态从 running 变为 completed/failed 时发送浏览器通知
        if (prevTask?.status === 'running' && (status === 'completed' || status === 'failed')) {
          try {
            if (Notification.permission === 'granted') {
              new Notification(
                status === 'completed' ? '任务完成' : '任务失败',
                {
                  body: status === 'completed'
                    ? `类目 ${prevTask.browse_node_id} 分析完成`
                    : `类目 ${prevTask.browse_node_id} 分析失败：${error || '未知错误'}`,
                }
              )
            } else if (Notification.permission !== 'denied') {
              Notification.requestPermission()
            }
          } catch (e) { /* 通知失败不影响主流程 */ }
        }
        return prev.map((t) => (t.id === task_id ? { ...t, status, error } : t))
      })
      return
    }
    if (event === 'error') {
      const { message } = data as { message: string }
      toast.error(message || 'SSE 连接错误')
      return
    }
    if (event === 'cost_update') {
      const { cost_usd } = data as { cost_usd: number }
      setCurrentCost(cost_usd)
      return
    }
    // 'log' events kept for future debug pane; ignored here for now.
    if (event === 'refresh_changed') {
      return
    }
  }, [])

  // ── Switch active task ───────────────────────────────────────────────────
  const selectTask = useCallback(async (id: string) => {
    // 取消上一次未完成的 selectTask，防止快速切换时竞态
    selectAbortRef.current?.abort()
    const controller = new AbortController()
    selectAbortRef.current = controller

    setActiveId(id)
    resetTaskState()
    sseRef.current?.close()

    const list = await loadTasks()
    if (controller.signal.aborted) return
    const task = list.find((t) => t.id === id)
    if (!task) return

    // Always load reports (works for any task state — may return empty)
    try {
      const r = await api.getReports(id)
      if (controller.signal.aborted) return
      setReports({ path: r.workspace_path, data: r.reports })
      setPhases(r.phases)
    } catch (e) {
      console.error('加载报告失败:', e)
      toast.error('加载报告失败')
    }

    // Load persisted conversation history from SQLite
    try {
      const history = await api.getHistory(id)
      if (controller.signal.aborted) return
      if (history.stream_items.length > 0) {
        const itemsMap: Record<string, StreamItem> = {}
        for (const item of history.stream_items) {
          itemsMap[item.id] = item
        }
        setStreamItems(itemsMap)
        setStreamOrder(history.stream_order)
      }
      if (history.chat_messages.length > 0) {
        const msgs: LocalMessage[] = history.chat_messages.map((m) => ({
          id: `chat-${m.id}`,
          role: m.role,
          content: m.content,
          streaming: false,
        }))
        setLocalMessages(msgs)
      }
    } catch (e) {
      console.error('加载历史记录失败:', e)
      toast.error('加载历史记录失败')
    }

    // Only open SSE for live tasks
    if (task.status === 'completed' || task.status === 'failed') {
      return
    }

    const es = openProgressSSE(
      id,
      (e) => applySSEEvent(e.event, e.data),
      async () => {
        const updated = await loadTasks()
        const ut = updated.find((t) => t.id === id)
        if (ut) {
          try {
            const r = await api.getReports(id)
            setReports({ path: r.workspace_path, data: r.reports })
            setPhases(r.phases)
          } catch (e) { console.error('SSE 完成后加载报告失败:', e) }
        }
      }
    )
    sseRef.current = es
  }, [applySSEEvent, loadTasks, resetTaskState, toast])

  useEffect(() => () => { sseRef.current?.close() }, [])

  // ── Create task ──────────────────────────────────────────────────────────
  const handleCreate = async (url: string, modelFamily?: ModelFamily) => {
    setCreating(true)
    try {
      const task = await api.createTask(url, modelFamily ? { modelFamily } : undefined)
      await loadTasks()
      setShowDialog(false)
      setView('workspace')
      selectTask(task.id)
    } catch (err) {
      toast.error(`创建任务失败：${err}`)
    } finally {
      setCreating(false)
    }
  }

  // ── Delete task ──────────────────────────────────────────────────────────
  const handleDelete = async (id: string) => {
    if (!confirm('确定删除这条任务记录？')) return
    await api.deleteTask(id)
    if (activeId === id) {
      setActiveId(null)
      resetTaskState()
      sseRef.current?.close()
    }
    await loadTasks()
  }

  // ── Resume task ──────────────────────────────────────────────────────────
  const handleResume = async () => {
    if (!activeId) return
    setResuming(true)
    try {
      await api.resumeTask(activeId)
      await loadTasks()
      await selectTask(activeId)
    } catch (err) {
      toast.error(`继续分析失败：${err}`)
    } finally {
      setResuming(false)
    }
  }

  // ── Refresh task (incremental update) ────────────────────────────────────
  const handleRefresh = async () => {
    if (!activeId) return
    setRefreshing(true)
    try {
      // 乐观更新本地 task 状态，让 cancel 按钮立即可见
      const updatedTask = await api.refreshTask(activeId)
      setTasks((prev) => prev.map((t) => t.id === activeId ? updatedTask : t))

      // 追加系统消息，不清空现有 streamItems/streamOrder
      const msgId = `sys-refresh-${Date.now()}`
      setStreamItems((prev) => ({ ...prev, [msgId]: {
        id: msgId, kind: 'system_note', content: '🔄 正在刷新排名，获取最新榜单数据...', timestamp: new Date().toISOString(), final: true,
      }}))
      setStreamOrder((prev) => (prev.includes(msgId) ? prev : [...prev, msgId]))

      // 建立 SSE 连接接收刷新进度，不调用 selectTask 以避免清空 streamItems
      sseRef.current?.close()
      let changedCount = 0
      const es = openProgressSSE(
        activeId,
        (e) => {
          applySSEEvent(e.event, e.data)
          // 从 SSE 事件捕获 changed_count（不从 Task 对象读取，因为 Task 没有此字段）
          if (e.event === 'refresh_changed') {
            changedCount = (e.data as { changed_count: number }).changed_count
          }
        },
        async () => {
          const refreshed = await loadTasks()
          const ut = refreshed.find((t) => t.id === activeId)
          if (ut) setTasks((prev) => prev.map((t) => t.id === activeId ? ut : t))
          // 刷新完成，追加完成消息
          const doneId = `sys-refresh-done-${Date.now()}`
          const doneContent = changedCount > 0
            ? `✅ 排名已更新，${changedCount} 个 ASIN 排名有变化`
            : `✅ 排名已更新，暂无变化`
          setStreamItems((prev) => ({ ...prev, [doneId]: {
            id: doneId, kind: 'system_note', content: doneContent, timestamp: new Date().toISOString(), final: true,
          }}))
          setStreamOrder((prev) => (prev.includes(doneId) ? prev : [...prev, doneId]))
        }
      )
      sseRef.current = es
    } catch (err) {
      toast.error(`刷新排名失败：${err}`)
    } finally {
      setRefreshing(false)
    }
  }

  // ── Reanalyze task (full reset) ─────────────────────────────────────────
  const handleReanalyze = async () => {
    if (!activeId) return
    setShowReanalyzeConfirm(false)
    setReanalyzing(true)
    try {
      await api.reanalyzeTask(activeId)
      await loadTasks()
      await selectTask(activeId)
    } catch (err) {
      toast.error(`全量重新分析失败：${err}`)
    } finally {
      setReanalyzing(false)
    }
  }

  // ── Cancel task ──────────────────────────────────────────────────────────
  const handleCancel = async () => {
    if (!activeId) return
    if (!confirm('确定取消此任务？已完成的中间数据将保留。')) return
    setCancelling(true)
    try {
      await api.cancelTask(activeId)
      const refreshed = await loadTasks()
      const ut = refreshed.find((t) => t.id === activeId)
      if (ut) setTasks((prev) => prev.map((t) => t.id === activeId ? ut : t))
      // 取消后不调用 selectTask（会清空 SSE），直接更新 phases 让继续分析按钮可见
      setPhases((prev) => ({ ...prev }))
      toast.success('任务已取消')
    } catch (err) {
      toast.error(`取消任务失败：${err}`)
    } finally {
      setCancelling(false)
    }
  }

  // ── Refresh reports (manual) ─────────────────────────────────────────────
  const refreshReports = async () => {
    if (!activeId) return
    try {
      const r = await api.getReports(activeId)
      setReports({ path: r.workspace_path, data: r.reports })
      setPhases(r.phases)
    } catch (e) { console.error('刷新报告失败:', e) }
  }

  // ── Q&A send ─────────────────────────────────────────────────────────────
  const handleSendChat = async (text: string) => {
    if (!activeId) return
    const userId = crypto.randomUUID()
    const asstId = crypto.randomUUID()
    setLocalMessages((prev) => [
      ...prev,
      { id: userId, role: 'user', content: text },
      { id: asstId, role: 'assistant', content: '', streaming: true },
    ])
    try {
      for await (const chunk of streamChat(activeId, text)) {
        setLocalMessages((prev) =>
          prev.map((m) => (m.id === asstId ? { ...m, content: m.content + chunk } : m))
        )
      }
    } catch (err) {
      setLocalMessages((prev) =>
        prev.map((m) => (m.id === asstId ? { ...m, content: `❌ 请求失败：${err}`, streaming: false } : m))
      )
    }
    setLocalMessages((prev) =>
      prev.map((m) => (m.id === asstId ? { ...m, streaming: false } : m))
    )
  }

  const canAsk = phases.qa && activeTask?.status !== 'pending'
  const hasAnyReport = reports && Object.values(reports.data).some(Boolean)

  // 未登录时显示认证页面
  if (authChecked && !user) {
    return (
      <div className="flex items-center justify-center w-full min-h-screen bg-[var(--bg-deep)]">
        <AuthPage onAuth={handleAuth} />
      </div>
    )
  }

  // 加载中
  if (!authChecked) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--bg-deep)]">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
          <p className="text-[var(--text-tertiary)] text-sm">加载中...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="app-shell flex w-full h-screen bg-[var(--bg-deep)] overflow-hidden">
      <Sidebar
        tasks={tasks}
        activeId={activeId}
        activeView={view}
        onSelect={(id) => {
          setView('workspace')
          selectTask(id)
        }}
        onNew={() => {
          setView('workspace')
          setShowDialog(true)
        }}
        onDelete={handleDelete}
        user={user}
        onLogout={handleLogout}
        onSettings={() => setView('settings')}
        onWorkspace={() => setView('workspace')}
        statusFilter={statusFilter}
        keywordFilter={keywordFilter}
        onStatusFilterChange={setStatusFilter}
        onKeywordFilterChange={setKeywordFilter}
      />

      {view === 'settings' ? (
        <div className="app-main-panel flex-1 overflow-y-auto">
          <SettingsView onClose={() => setView('workspace')} onEnterEdit={() => setShowDialog(false)} />
        </div>
      ) : !activeTask ? (
        <main className="app-main-panel flex-1 flex flex-col min-h-screen overflow-hidden">
          <EmptyState onCreate={handleCreate} creating={creating} />
        </main>
      ) : (
        <>
          {/* Center main area */}
          <main className="app-main-panel flex-1 flex flex-col min-w-0 h-screen">
            {/* Top bar */}
            <div className="border-b border-[var(--border-default)] px-5 py-2.5 bg-[var(--bg-raised)] flex items-center gap-2.5">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-[var(--text-primary)] truncate flex items-center gap-2">
                  类目 {activeTask.browse_node_id}
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${
                      activeTask.status === 'running'
                        ? 'bg-[var(--accent-muted)] text-[var(--accent)]'
                        : activeTask.status === 'completed'
                        ? 'bg-[var(--success-muted)] text-[var(--success)]'
                        : activeTask.status === 'failed'
                        ? 'bg-[var(--error-muted)] text-[var(--error)]'
                        : 'bg-[var(--bg-surface)] text-[var(--text-tertiary)]'
                    }`}
                  >
                    {activeTask.status}
                  </span>
                </p>
                <p className="text-[11px] text-[var(--text-tertiary)] truncate mt-0.5">{activeTask.url}</p>
                {activeTask.error && (
                  <p className="text-[11px] text-[var(--error)] truncate mt-0.5" title={activeTask.error}>
                    错误：{activeTask.error.split('\n')[0]}
                  </p>
                )}
                {currentCost > 0 && (
                  <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
                    成本：${currentCost.toFixed(4)}
                  </p>
                )}
              </div>

              {(activeTask.status === 'failed' ||
                (activeTask.status === 'running' && !phases.summary) ||
                (activeTask.status === 'completed' && !phases.summary)) && (
                <button
                  onClick={handleResume}
                  disabled={resuming}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-[var(--radius-sm)] bg-[var(--bg-surface)] hover:bg-[var(--bg-overlay)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer disabled:opacity-50 transition-all duration-150 border border-[var(--border-default)]"
                >
                  <RotateCcw size={11} className={resuming ? 'animate-spin' : ''} />
                  继续分析
                </button>
              )}

              {activeTask.status === 'completed' && phases.summary && (
                <button
                  onClick={handleRefresh}
                  disabled={refreshing}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-[var(--radius-sm)] bg-[var(--bg-surface)] hover:bg-[var(--bg-overlay)] text-[var(--text-secondary)] cursor-pointer disabled:opacity-50 transition-all duration-150 border border-[var(--border-default)]"
                >
                  <RefreshCcw size={11} className={refreshing ? 'animate-spin' : ''} />
                  {refreshing ? '刷新中...' : '刷新排名'}
                </button>
              )}

              {(activeTask.status === 'running' || activeTask.status === 'pending') && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-[var(--radius-sm)] bg-[var(--error-muted)] hover:bg-[var(--error)]/10 text-[var(--error)] cursor-pointer disabled:opacity-50 transition-all duration-150 border border-[var(--error)]/20"
                >
                  <AlertTriangle size={11} className={cancelling ? 'animate-spin' : ''} />
                  取消任务
                </button>
              )}

              {refreshing && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-[var(--radius-sm)] bg-[var(--error)] hover:bg-[var(--error)]/90 text-white cursor-pointer disabled:opacity-50 transition-all duration-150 border border-[var(--error)]/20"
                >
                  <AlertTriangle size={11} className={cancelling ? 'animate-spin' : ''} />
                  取消刷新
                </button>
              )}

              {(activeTask.status === 'completed' || activeTask.status === 'failed') && (
                <button
                  onClick={() => setShowReanalyzeConfirm(true)}
                  disabled={reanalyzing}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-[var(--radius-sm)] bg-[var(--error-muted)] hover:bg-[var(--error)]/10 text-[var(--error)] cursor-pointer disabled:opacity-50 transition-all duration-150 border border-[var(--error)]/20"
                >
                  <AlertTriangle size={11} className={reanalyzing ? 'animate-spin' : ''} />
                  全量重新分析
                </button>
              )}

              <button
                onClick={() => {
                  refreshReports()
                  setShowReports(true)
                }}
                disabled={!hasAnyReport}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-[var(--radius-sm)] bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-150"
              >
                <FileText size={11} />
                报告 & 下载
              </button>
            </div>

            {/* Live stream body + input */}
            <div className="flex-1 min-h-0">
              <LiveStream
                items={itemsArray}
                localMessages={localMessages}
                onSend={handleSendChat}
                canAsk={Boolean(canAsk)}
                isTaskRunning={activeTask.status === 'running' || activeTask.status === 'pending'}
                task={activeTask}
              />
            </div>
          </main>

          {/* Right stage rail */}
          <div className="stage-rail-shell">
            <StageRail
              task={activeTask}
              phases={phases}
              catalog={stageCatalog}
              currentItem={currentItem}
            />
          </div>

          {/* Reports overlay */}
          {showReports && (
            <div
              className="fixed inset-0 bg-black/70 backdrop-blur-sm z-40 flex items-center justify-center p-6 animate-fade-in-scale"
              onClick={() => setShowReports(false)}
            >
              <div
                className="bg-[var(--bg-base)] border border-[var(--border-default)] rounded-[var(--radius-xl)] shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-between px-5 py-3.5 border-b border-[var(--border-default)]">
                  <h2 className="text-sm font-semibold text-[var(--text-primary)]">报告 & 下载</h2>
                  <button
                    onClick={() => setShowReports(false)}
                    className="text-[var(--text-tertiary)] hover:text-[var(--text-primary)] cursor-pointer p-1 rounded-[var(--radius-sm)] hover:bg-[var(--bg-elevated)] transition-colors"
                  >
                    <X size={16} />
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto p-5">
                  {reports ? (
                    <ReportViewer
                      taskId={activeTask.id}
                      browseNodeId={activeTask.browse_node_id}
                      reports={reports.data}
                    />
                  ) : (
                    <div className="text-center text-[var(--text-tertiary)] text-sm py-16">
                      报告尚未就绪
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {/* Toast notifications */}
      <ToastContainer toasts={toast.toasts} onDismiss={toast.dismiss} />

      {/* New task dialog */}
      {showDialog && (
        <NewTaskDialog
          onSubmit={handleCreate}
          onClose={() => setShowDialog(false)}
          loading={creating}
        />
      )}

      {/* Reanalyze confirmation dialog */}
      {showReanalyzeConfirm && (
        <div
          className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6 animate-fade-in-scale"
          onClick={() => setShowReanalyzeConfirm(false)}
        >
          <div
            className="bg-[var(--bg-base)] border border-red-900/40 rounded-[var(--radius-xl)] shadow-2xl w-full max-w-md flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 px-5 py-4 border-b border-[var(--border-default)]">
              <div className="w-8 h-8 rounded-[var(--radius-md)] bg-[var(--error-muted)] flex items-center justify-center">
                <AlertTriangle size={16} className="text-[var(--error)]" />
              </div>
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">全量重新分析</h2>
            </div>
            <div className="px-5 py-4 text-sm text-[var(--text-secondary)] leading-relaxed">
              此操作将<strong className="text-[var(--error)]">清除该类目的所有已有数据</strong>
              （爬取结果、分块、分析报告等），并从头开始全新分析。
              已生成的报告将<strong className="text-[var(--error)]">不可恢复</strong>。
            </div>
            <div className="flex items-center justify-end gap-3 px-5 py-3 border-t border-[var(--border-default)]">
              <button
                onClick={() => setShowReanalyzeConfirm(false)}
                className="px-4 py-2 text-xs rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] hover:bg-[var(--bg-surface)] text-[var(--text-secondary)] cursor-pointer transition-colors border border-[var(--border-default)]"
              >
                取消
              </button>
              <button
                onClick={handleReanalyze}
                disabled={reanalyzing}
                className="px-4 py-2 text-xs rounded-[var(--radius-sm)] bg-red-600 hover:bg-red-500 text-white cursor-pointer disabled:opacity-50 transition-colors"
              >
                确认全量重新分析
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
