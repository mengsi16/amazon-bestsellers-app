import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowUp, Loader2, ChevronDown, ChevronRight, ChevronUp,
  Wrench, Cpu, CheckCircle2, XCircle, AlertTriangle, Info, Settings2,
} from 'lucide-react'
import type { ModelConfig, StreamItem, Task } from '../api'
import type { TimelineEntry } from '../timeline'

interface Props {
  timelineEntries: TimelineEntry[]
  onSend: (text: string) => void
  canAsk: boolean
  isTaskRunning: boolean
  task: Task | null
  modelConfigs: ModelConfig[]
  selectedModelConfigId: string | null
  onModelConfigChange: (id: string) => void
  onOpenSettings?: () => void
}

function SystemNote({ content }: { content: string }) {
  return (
    <div className="flex justify-center my-2">
      <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[var(--bg-overlay)] border border-[var(--border-default)] text-xs text-[var(--text-tertiary)]">
        <Info size={11} />
        {content}
      </div>
    </div>
  )
}

function AssistantText({ content, streaming }: { content: string; streaming: boolean }) {
  if (!content && !streaming) return null
  return (
    <div className="mx-auto w-full max-w-3xl">
      <div className="min-w-0">
        <div className="prose prose-sm max-w-none text-[var(--text-secondary)] leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {content || '▍'}
          </ReactMarkdown>
        </div>
        {streaming && (
          <span className="inline-block w-1 h-3.5 bg-[var(--accent)] animate-pulse ml-0.5 align-text-bottom" />
        )}
      </div>
    </div>
  )
}

function Thinking({ content, streaming }: { content: string; streaming: boolean }) {
  const [open, setOpen] = useState(false)
  if (!content.trim() && !streaming) return null
  return (
    <div className="mx-auto my-1 w-full max-w-3xl">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-[var(--text-disabled)] hover:text-[var(--text-tertiary)] cursor-pointer"
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        执行细节 {streaming && '...'}
      </button>
      {open && (
        <div className="mt-1 ml-4 pl-3 border-l border-[var(--border-default)] text-xs text-[var(--text-disabled)] italic whitespace-pre-wrap">
          {content || '…'}
        </div>
      )}
    </div>
  )
}

function toolIconFor(name?: string) {
  if (name === 'Task' || name === 'Agent') return <Cpu size={12} className="text-[var(--success)]" />
  return <Wrench size={12} className="text-amber-500" />
}

function ToolCall({ item }: { item: StreamItem }) {
  const [expanded, setExpanded] = useState(false)
  const meta = item.meta || {}
  const isSub = Boolean(meta.is_subagent)
  const status = meta.status || 'starting'
  const isError = status === 'error' || meta.is_error
  const isDone = status === 'done' || status === 'completed' || item.final
  const isKilled = status === 'killed' || status === 'stopped'

  const statusChip = (() => {
    if (isError) return <span className="flex items-center gap-0.5 text-[var(--error)] text-[10px]"><XCircle size={10} /> error</span>
    if (isKilled) return <span className="flex items-center gap-0.5 text-amber-500 text-[10px]"><AlertTriangle size={10} /> killed</span>
    if (isDone) return <span className="flex items-center gap-0.5 text-[var(--success)] text-[10px]"><CheckCircle2 size={10} /> done</span>
    return <span className="flex items-center gap-0.5 text-[var(--accent)] text-[10px]"><Loader2 size={10} className="animate-spin" /> running</span>
  })()

  const activities = meta.subagent_activities || []

  return (
    <div className="mx-auto my-1 w-full max-w-3xl">
      <div
        className={`rounded-[var(--radius-md)] border ${
          isSub
            ? 'border-[var(--success)]/20 bg-[var(--success-muted)]'
            : isError
            ? 'border-[var(--error)]/20 bg-[var(--error-muted)]'
            : 'border-[var(--border-default)] bg-[var(--bg-overlay)]'
        }`}
      >
        <button
          onClick={() => setExpanded((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2 text-left cursor-pointer hover:bg-[var(--bg-surface)]/50 rounded-[var(--radius-md)] transition-colors"
        >
          {expanded ? <ChevronDown size={11} className="text-[var(--text-disabled)]" /> : <ChevronRight size={11} className="text-[var(--text-disabled)]" />}
          {toolIconFor(meta.tool_name)}
          <span className={`text-xs font-medium ${isSub ? 'text-[var(--success)]' : 'text-amber-500'}`}>
            {meta.tool_name || 'Tool'}
          </span>
          <span className="text-xs text-[var(--text-tertiary)] truncate flex-1 font-mono">
            {meta.input_summary || item.content || '…'}
          </span>
          {statusChip}
        </button>

        {expanded && (
          <div className="px-3 pb-3 border-t border-[var(--border-default)] pt-2 space-y-2">
            {meta.input_summary && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-[var(--text-disabled)] mb-1">Input</p>
                <pre className="text-xs text-[var(--text-secondary)] bg-[var(--bg-deep)] rounded-[var(--radius-sm)] px-2 py-1.5 overflow-x-auto whitespace-pre-wrap break-all font-mono">
                  {meta.input_summary}
                </pre>
              </div>
            )}

            {isSub && activities.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-[var(--text-disabled)] mb-1">
                  Sub-agent activities ({activities.length})
                </p>
                <div className="space-y-1 max-h-44 overflow-y-auto">
                  {activities.slice(-15).map((a, i) => (
                    <div key={i} className="text-xs text-[var(--text-tertiary)] flex items-start gap-2">
                      <Loader2 size={9} className="text-[var(--success)]/60 shrink-0 mt-0.5" />
                      <span className="flex-1 min-w-0 break-words">
                        {a.description}
                        {typeof a.duration_ms === 'number' && a.duration_ms > 0 && (
                          <span className="text-[var(--text-disabled)] ml-1.5">
                            ({Math.round(a.duration_ms / 1000)}s)
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {meta.result_summary && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-[var(--text-disabled)] mb-1">Result</p>
                <pre className="text-xs text-[var(--text-secondary)] bg-[var(--bg-deep)] rounded-[var(--radius-sm)] px-2 py-1.5 overflow-x-auto whitespace-pre-wrap break-all">
                  {meta.result_summary}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function FinalResult({ item }: { item: StreamItem }) {
  const meta = item.meta || {}
  const dur = meta.duration_ms ? `${(meta.duration_ms / 1000).toFixed(1)}s` : null
  const cost = meta.total_cost_usd ? `$${meta.total_cost_usd.toFixed(2)}` : null
  const turns = meta.num_turns
  const isError = Boolean(meta.is_error)
  const hasContent = item.content.trim().length > 0

  if (!hasContent) {
    return (
      <div className="my-4 flex justify-center">
        <div
          className={`inline-flex items-center gap-3 px-4 py-1.5 rounded-full border text-xs ${
            isError
              ? 'border-[var(--error)]/30 bg-[var(--error-muted)] text-[var(--error)]'
              : 'border-[var(--success)]/30 bg-[var(--success-muted)] text-[var(--success)]'
          }`}
        >
          <Info size={11} />
          <span className="font-medium">{isError ? '任务失败' : '任务完成'}</span>
          {dur && <span className="opacity-60">用时 {dur}</span>}
          {cost && <span className="opacity-60">成本 {cost}</span>}
          {typeof turns === 'number' && <span className="opacity-60">{turns} turns</span>}
        </div>
      </div>
    )
  }

  return (
    <div
      className={`my-4 rounded-[var(--radius-lg)] border overflow-hidden ${
        isError
          ? 'border-[var(--error)]/30 bg-[var(--error-muted)]'
          : 'border-[var(--success)]/30 bg-[var(--success-muted)]'
      }`}
    >
      <div
        className={`px-4 py-2 border-b flex items-center gap-2 ${
          isError ? 'border-[var(--error)]/20 bg-[var(--error-muted)]' : 'border-[var(--success)]/20 bg-[var(--success-muted)]'
        }`}
      >
        <Info size={13} className={isError ? 'text-[var(--error)]' : 'text-[var(--success)]'} />
        <span className={`text-xs font-medium ${isError ? 'text-[var(--error)]' : 'text-[var(--success)]'}`}>
          {isError ? '失败' : '最终报告'}
        </span>
        <div className={`ml-auto flex items-center gap-3 text-[10px] ${isError ? 'text-[var(--error)]/70' : 'text-[var(--success)]/70'}`}>
          {dur && <span>用时 {dur}</span>}
          {cost && <span>成本 {cost}</span>}
          {typeof turns === 'number' && <span>{turns} turns</span>}
        </div>
      </div>
      <div className="p-4 prose prose-sm max-w-none text-[var(--text-secondary)] leading-relaxed">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>
      </div>
    </div>
  )
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex gap-3 justify-end">
      <div className="max-w-[80%] rounded-[var(--radius-md)] border border-[var(--border-default)] bg-[var(--bg-overlay)] px-4 py-2.5 text-sm text-[var(--text-primary)] whitespace-pre-wrap">
        {content}
      </div>
    </div>
  )
}

export default function LiveStream({
  timelineEntries,
  onSend,
  canAsk,
  isTaskRunning,
  task,
  modelConfigs,
  selectedModelConfigId,
  onModelConfigChange,
  onOpenSettings,
}: Props) {
  const [input, setInput] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const [configMenuOpen, setConfigMenuOpen] = useState(false)
  const [configMenuStyle, setConfigMenuStyle] = useState<CSSProperties>({})
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const composerRef = useRef<HTMLDivElement>(null)
  const configButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [timelineEntries, autoScroll])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setAutoScroll(distFromBottom < 60)
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text || !canAsk) return
    setInput('')
    const el = textareaRef.current
    if (el) el.style.height = 'auto'
    onSend(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const autoResize = () => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`
    }
  }

  const toggleConfigMenu = () => {
    const composerRect = composerRef.current?.getBoundingClientRect()
    const rect = configButtonRef.current?.getBoundingClientRect()
    if (composerRect && rect) {
      const placement = rect.top > window.innerHeight / 2 ? 'up' : 'down'
      setConfigMenuStyle({
        left: composerRect.left,
        width: composerRect.width,
        top: placement === 'down' ? composerRect.bottom + 8 : undefined,
        bottom: placement === 'up' ? window.innerHeight - composerRect.top + 8 : undefined,
      })
    }
    setConfigMenuOpen((open) => !open)
  }

  const chooseConfig = (id: string) => {
    onModelConfigChange(id)
    setConfigMenuOpen(false)
  }

  const isEmpty = timelineEntries.length === 0
  const selectedConfig = useMemo(
    () => modelConfigs.find((config) => config.id === selectedModelConfigId)
      ?? modelConfigs.find((config) => config.is_default)
      ?? modelConfigs[0]
      ?? null,
    [modelConfigs, selectedModelConfigId],
  )

  const placeholder = useMemo(() => {
    if (!canAsk) {
      return isTaskRunning
        ? '报告生成后可继续追问'
        : '选择已完成任务后继续追问'
    }
    return '询问这个类目的机会、风险或下一步动作'
  }, [canAsk, isTaskRunning])

  return (
    <div className="flex flex-col h-full bg-[var(--bg-raised)]">
      {/* Stream body */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-5 py-5 space-y-2.5"
      >
        {isEmpty ? (
          <div className="h-full flex flex-col items-center justify-center text-[var(--text-disabled)] text-sm gap-3 px-8 text-center">
            <p>
              {isTaskRunning
                ? '分析流水线启动中，实时输出马上就来…'
                : task?.status === 'failed'
                ? '任务失败，查看右侧阶段条和日志定位问题。'
                : '暂无对话内容。'}
            </p>
          </div>
        ) : (
          <>
            {timelineEntries.map((entry) => {
              if (entry.source === 'chat') {
                const message = entry.message
                return message.role === 'user' ? (
                  <UserBubble key={entry.id} content={message.content} />
                ) : (
                  <AssistantText
                    key={entry.id}
                    content={message.content}
                    streaming={!!message.streaming}
                  />
                )
              }

              const item = entry.item
              switch (item.kind) {
                case 'system_note':
                  return <SystemNote key={entry.id} content={item.content} />
                case 'assistant_text':
                  return (
                    <AssistantText
                      key={entry.id}
                      content={item.content}
                      streaming={!item.final}
                    />
                  )
                case 'thinking':
                  return (
                    <Thinking
                      key={entry.id}
                      content={item.content}
                      streaming={!item.final}
                    />
                  )
                case 'tool_call':
                  return <ToolCall key={entry.id} item={item} />
                case 'final_result':
                  return <FinalResult key={entry.id} item={item} />
                default:
                  return null
              }
            })}
          </>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Auto-scroll notice */}
      {!autoScroll && (
        <div className="px-5 pb-1 flex justify-center">
          <button
            onClick={() => {
              setAutoScroll(true)
              bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
            }}
            className="text-[11px] text-[var(--accent)] hover:text-[var(--accent-emphasis)] cursor-pointer underline"
          >
            ↓ 跳到最新
          </button>
        </div>
      )}

      {/* Input bar */}
      <div className="border-t border-[var(--border-default)] bg-[var(--bg-raised)] px-5 py-3">
        <div
          ref={composerRef}
          className={`mx-auto max-w-3xl border rounded-[var(--radius-md)] bg-[var(--bg-elevated)] shadow-sm transition-all ${
            !canAsk
              ? 'border-[var(--border-default)] opacity-65'
              : 'border-[var(--border-default)] focus-within:border-[var(--accent)] focus-within:ring-2 focus-within:ring-[var(--accent-glow)]'
          }`}
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value)
              autoResize()
            }}
            onKeyDown={handleKeyDown}
            disabled={!canAsk}
            placeholder={placeholder}
            rows={1}
            className="block min-h-[44px] w-full resize-none bg-transparent px-4 pb-2 pt-3 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-disabled)] disabled:cursor-not-allowed"
          />
          <div className="relative flex items-center justify-between gap-2 rounded-b-[var(--radius-md)] px-3 pb-3">
            <div className="flex min-w-0 items-center gap-2">
              {modelConfigs.length > 0 ? (
                <button
                  ref={configButtonRef}
                  type="button"
                  onMouseDown={(event) => {
                    event.preventDefault()
                    toggleConfigMenu()
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      toggleConfigMenu()
                    }
                  }}
                  className="inline-flex max-w-[220px] items-center gap-1.5 rounded-[var(--radius-xs)] px-2 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-overlay)] hover:text-[var(--text-primary)]"
                  title="切换默认模型配置"
                >
                  <Settings2 size={12} className="shrink-0" />
                  <span className="truncate">{selectedConfig?.name || '选择配置'}</span>
                  <span className="shrink-0 text-[var(--text-disabled)]">
                    {selectedConfig?.default_model_family || 'sonnet'}
                  </span>
                  {configMenuOpen ? <ChevronUp size={12} className="shrink-0" /> : <ChevronDown size={12} className="shrink-0" />}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={onOpenSettings}
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-xs)] px-2 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-overlay)] hover:text-[var(--text-primary)]"
                >
                  <Settings2 size={12} />
                  添加模型配置
                </button>
              )}
              <span className="hidden text-[11px] text-[var(--text-disabled)] sm:inline">
                {canAsk ? '报告上下文' : isTaskRunning ? '报告生成中' : '追问未解锁'}
              </span>
            </div>

            {configMenuOpen && modelConfigs.length > 0 && (
              <div
                className="fixed z-[60] overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-default)] bg-[var(--bg-raised)] shadow-xl"
                style={configMenuStyle}
              >
                <div className="border-b border-[var(--border-default)] px-3.5 py-2.5">
                  <p className="text-xs font-medium text-[var(--text-primary)]">运行配置</p>
                  <p className="text-[11px] text-[var(--text-tertiary)]">用于新分析和未指定模型的 Agent 调用</p>
                </div>
                <div className="max-h-72 overflow-y-auto p-1.5">
                  {modelConfigs.map((config) => {
                    const active = config.id === selectedConfig?.id
                    const family = config.default_model_family
                    const model = family === 'opus' ? config.opus_model : config.sonnet_model
                    return (
                      <button
                        key={config.id}
                        type="button"
                        onClick={() => chooseConfig(config.id)}
                        className={`grid w-full grid-cols-[18px_minmax(0,1fr)_auto] items-start gap-2 rounded-[var(--radius-sm)] px-2.5 py-2 text-left transition-colors ${
                          active ? 'bg-[var(--accent-muted)]' : 'hover:bg-[var(--bg-overlay)]'
                        }`}
                      >
                        <CheckCircle2
                          size={13}
                          className={`mt-0.5 shrink-0 ${active ? 'text-[var(--accent)]' : 'text-[var(--text-disabled)]'}`}
                        />
                        <span className="min-w-0">
                          <span className="block text-xs font-medium text-[var(--text-primary)]">
                            {config.name}
                          </span>
                          <span className="mt-0.5 block break-all text-[11px] leading-relaxed text-[var(--text-tertiary)]">
                            {family}: {model}
                          </span>
                        </span>
                        {config.is_default && (
                          <span className="shrink-0 rounded-full bg-[var(--success-muted)] px-1.5 py-0.5 text-[10px] text-[var(--success)]">
                            默认
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}

            <button
              onClick={handleSend}
              disabled={!input.trim() || !canAsk}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--text-primary)] text-white transition-all hover:bg-black disabled:cursor-not-allowed disabled:opacity-35"
              title="发送"
            >
              <ArrowUp size={15} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
