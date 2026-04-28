import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { FileText, RotateCcw, RefreshCcw, AlertTriangle, X, Rocket } from 'lucide-react'
import { api, openProgressSSE, streamChat } from './api'
import type { Task, Phases, Reports, StreamItem, StageInfo } from './api'
import Sidebar from './components/Sidebar'
import ReportViewer from './components/ReportViewer'
import NewTaskDialog from './components/NewTaskDialog'
import StageRail from './components/StageRail'
import LiveStream, { type LocalMessage } from './components/LiveStream'

const EMPTY_PHASES: Phases = { crawl: false, chunk: false, analyze: false, summary: false, qa: false }

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <div className="text-6xl mb-6">🛒</div>
      <h1 className="text-2xl font-bold text-white mb-3">Amazon Bestsellers 分析助手</h1>
      <p className="text-slate-500 text-sm mb-8 max-w-md leading-relaxed">
        输入任意 Amazon Bestsellers 类目 URL，自动完成爬取、分块、四维度分析，生成完整的竞品洞察报告。
      </p>
      <button
        onClick={onNew}
        className="px-6 py-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-medium transition-colors text-sm cursor-pointer flex items-center gap-2"
      >
        <Rocket size={15} />
        开始第一次分析
      </button>
      <div className="mt-12 grid grid-cols-2 gap-4 max-w-lg text-left">
        {[
          { icon: '🏪', title: 'Marketplace', desc: '价格、评分、品牌集中度' },
          { icon: '💬', title: 'Reviews', desc: '情感分析、用户痛点洞察' },
          { icon: '🎨', title: 'A+ Content', desc: '竞品内容策略深度分析' },
          { icon: '🔍', title: 'Fine-Grained', desc: '细分类标签 & 机会空档' },
        ].map((item) => (
          <div
            key={item.title}
            className="p-4 bg-slate-800/40 border border-slate-700/50 rounded-xl"
          >
            <div className="text-2xl mb-2">{item.icon}</div>
            <p className="text-sm font-medium text-white">{item.title}</p>
            <p className="text-xs text-slate-500 mt-1">{item.desc}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [showDialog, setShowDialog] = useState(false)
  const [creating, setCreating] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [reanalyzing, setReanalyzing] = useState(false)
  const [showReanalyzeConfirm, setShowReanalyzeConfirm] = useState(false)
  const [showReports, setShowReports] = useState(false)

  // Per-task live state
  const [phases, setPhases] = useState<Phases>(EMPTY_PHASES)
  const [stageCatalog, setStageCatalog] = useState<StageInfo[] | null>(null)
  const [streamItems, setStreamItems] = useState<Record<string, StreamItem>>({})
  const [streamOrder, setStreamOrder] = useState<string[]>([])
  const [localMessages, setLocalMessages] = useState<LocalMessage[]>([])
  const [reports, setReports] = useState<{ path: string; data: Reports } | null>(null)

  const activeTask = tasks.find((t) => t.id === activeId) ?? null
  const sseRef = useRef<EventSource | null>(null)

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
  }, [])

  // ── Task list polling ────────────────────────────────────────────────────
  const loadTasks = useCallback(async () => {
    try {
      const list = await api.listTasks()
      setTasks(list)
      return list
    } catch {
      return []
    }
  }, [])

  useEffect(() => {
    loadTasks()
    const interval = setInterval(loadTasks, 10_000)
    return () => clearInterval(interval)
  }, [loadTasks])

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
      return
    }
    if (event === 'status') {
      const { status, task_id, error } = data as { status: Task['status']; task_id: string; error: string | null }
      setTasks((prev) => prev.map((t) => (t.id === task_id ? { ...t, status, error } : t)))
      return
    }
    // 'log' events kept for future debug pane; ignored here for now.
  }, [])

  // ── Switch active task ───────────────────────────────────────────────────
  const selectTask = useCallback(async (id: string) => {
    setActiveId(id)
    resetTaskState()
    sseRef.current?.close()

    const list = await loadTasks()
    const task = list.find((t) => t.id === id)
    if (!task) return

    // Always load reports (works for any task state — may return empty)
    try {
      const r = await api.getReports(id)
      setReports({ path: r.workspace_path, data: r.reports })
      setPhases(r.phases)
    } catch {}

    // Load persisted conversation history from SQLite
    try {
      const history = await api.getHistory(id)
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
    } catch {}

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
          } catch {}
        }
      }
    )
    sseRef.current = es
  }, [applySSEEvent, loadTasks, resetTaskState])

  useEffect(() => () => { sseRef.current?.close() }, [])

  // ── Create task ──────────────────────────────────────────────────────────
  const handleCreate = async (url: string, model?: string) => {
    setCreating(true)
    try {
      const task = await api.createTask(url, model)
      await loadTasks()
      setShowDialog(false)
      selectTask(task.id)
    } catch (err) {
      alert(`创建任务失败：${err}`)
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
      alert(`继续分析失败：${err}`)
    } finally {
      setResuming(false)
    }
  }

  // ── Refresh task (incremental update) ────────────────────────────────────
  const handleRefresh = async () => {
    if (!activeId) return
    setRefreshing(true)
    try {
      await api.refreshTask(activeId)
      await loadTasks()
      await selectTask(activeId)
    } catch (err) {
      alert(`刷新排名失败：${err}`)
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
      alert(`全量重新分析失败：${err}`)
    } finally {
      setReanalyzing(false)
    }
  }

  // ── Refresh reports (manual) ─────────────────────────────────────────────
  const refreshReports = async () => {
    if (!activeId) return
    try {
      const r = await api.getReports(activeId)
      setReports({ path: r.workspace_path, data: r.reports })
      setPhases(r.phases)
    } catch {}
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

  return (
    <div className="flex w-full h-screen bg-[#0f1117] overflow-hidden">
      <Sidebar
        tasks={tasks}
        activeId={activeId}
        onSelect={selectTask}
        onNew={() => setShowDialog(true)}
        onDelete={handleDelete}
      />

      {!activeTask ? (
        <main className="flex-1 flex flex-col min-h-screen overflow-hidden">
          <EmptyState onNew={() => setShowDialog(true)} />
        </main>
      ) : (
        <>
          {/* Center main area */}
          <main className="flex-1 flex flex-col min-w-0 h-screen">
            {/* Top bar */}
            <div className="border-b border-slate-800 px-6 py-3 bg-[#0b0e15] flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-white truncate">
                  类目 {activeTask.browse_node_id}
                  <span
                    className={`ml-2 inline-block px-2 py-0.5 rounded-full text-[10px] ${
                      activeTask.status === 'running'
                        ? 'bg-indigo-900/50 text-indigo-300'
                        : activeTask.status === 'completed'
                        ? 'bg-emerald-900/50 text-emerald-300'
                        : activeTask.status === 'failed'
                        ? 'bg-red-900/50 text-red-300'
                        : 'bg-slate-800 text-slate-400'
                    }`}
                  >
                    {activeTask.status}
                  </span>
                </p>
                <p className="text-[11px] text-slate-500 truncate">{activeTask.url}</p>
              </div>

              {(activeTask.status === 'failed' ||
                (activeTask.status === 'running' && !phases.summary) ||
                (activeTask.status === 'completed' && !phases.summary)) && (
                <button
                  onClick={handleResume}
                  disabled={resuming}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 cursor-pointer disabled:opacity-50"
                >
                  <RotateCcw size={12} className={resuming ? 'animate-spin' : ''} />
                  继续分析
                </button>
              )}

              {activeTask.status === 'completed' && phases.summary && (
                <button
                  onClick={handleRefresh}
                  disabled={refreshing}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-amber-900/40 hover:bg-amber-800/50 text-amber-200 cursor-pointer disabled:opacity-50"
                >
                  <RefreshCcw size={12} className={refreshing ? 'animate-spin' : ''} />
                  刷新排名
                </button>
              )}

              {(activeTask.status === 'completed' || activeTask.status === 'failed') && (
                <button
                  onClick={() => setShowReanalyzeConfirm(true)}
                  disabled={reanalyzing}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-red-900/40 hover:bg-red-800/50 text-red-200 cursor-pointer disabled:opacity-50"
                >
                  <AlertTriangle size={12} className={reanalyzing ? 'animate-spin' : ''} />
                  全量重新分析
                </button>
              )}

              <button
                onClick={() => {
                  refreshReports()
                  setShowReports(true)
                }}
                disabled={!hasAnyReport}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-indigo-900/50 hover:bg-indigo-800/60 text-indigo-200 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <FileText size={12} />
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
          <StageRail
            task={activeTask}
            phases={phases}
            catalog={stageCatalog}
            currentItem={currentItem}
          />

          {/* Reports overlay */}
          {showReports && (
            <div
              className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-6"
              onClick={() => setShowReports(false)}
            >
              <div
                className="bg-[#0f1117] border border-slate-800 rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-between px-5 py-3 border-b border-slate-800">
                  <h2 className="text-sm font-semibold text-white">报告 & 下载</h2>
                  <button
                    onClick={() => setShowReports(false)}
                    className="text-slate-400 hover:text-white cursor-pointer"
                  >
                    <X size={18} />
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
                    <div className="text-center text-slate-500 text-sm py-16">
                      报告尚未就绪
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </>
      )}

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
          className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-6"
          onClick={() => setShowReanalyzeConfirm(false)}
        >
          <div
            className="bg-[#0f1117] border border-red-900/50 rounded-2xl shadow-2xl w-full max-w-md flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 px-5 py-4 border-b border-slate-800">
              <AlertTriangle size={20} className="text-red-400 shrink-0" />
              <h2 className="text-sm font-semibold text-white">全量重新分析</h2>
            </div>
            <div className="px-5 py-4 text-sm text-slate-300 leading-relaxed">
              此操作将<strong className="text-red-300">清除该类目的所有已有数据</strong>
              （爬取结果、分块、分析报告等），并从头开始全新分析。
              已生成的报告将<strong className="text-red-300">不可恢复</strong>。
            </div>
            <div className="flex items-center justify-end gap-3 px-5 py-3 border-t border-slate-800">
              <button
                onClick={() => setShowReanalyzeConfirm(false)}
                className="px-4 py-2 text-xs rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 cursor-pointer"
              >
                取消
              </button>
              <button
                onClick={handleReanalyze}
                disabled={reanalyzing}
                className="px-4 py-2 text-xs rounded-lg bg-red-600 hover:bg-red-500 text-white cursor-pointer disabled:opacity-50"
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
