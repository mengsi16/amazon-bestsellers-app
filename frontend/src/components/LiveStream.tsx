import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Send, Loader2, ChevronDown, ChevronRight,
  Wrench, Cpu, CheckCircle2, XCircle, AlertTriangle, Info,
} from 'lucide-react'
import type { StreamItem, Task } from '../api'

export interface LocalMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
}

interface Props {
  items: StreamItem[]
  localMessages: LocalMessage[]
  onSend: (text: string) => void
  canAsk: boolean
  isTaskRunning: boolean
  task: Task | null
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
    <div className="flex gap-3">
      <div className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--text-tertiary)]" />
      <div className="flex-1 min-w-0 max-w-[calc(100%-1rem)]">
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
    <div className="ml-10 my-1">
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
    <div className="ml-10 my-1">
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
  items,
  localMessages,
  onSend,
  canAsk,
  isTaskRunning,
  task,
}: Props) {
  const [input, setInput] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const sseItems = items

  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [sseItems.length, localMessages, autoScroll])

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

  const isEmpty = sseItems.length === 0 && localMessages.length === 0

  const placeholder = useMemo(() => {
    if (!canAsk) {
      return isTaskRunning
        ? '分析进行中，summary.md 写出后可在此追问…'
        : '分析完成后可在此追问…'
    }
    return '就报告内容追问（Enter 发送，Shift+Enter 换行）'
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
            {sseItems.map((item) => {
              switch (item.kind) {
                case 'system_note':
                  return <SystemNote key={item.id} content={item.content} />
                case 'assistant_text':
                  return (
                    <AssistantText
                      key={item.id}
                      content={item.content}
                      streaming={!item.final}
                    />
                  )
                case 'thinking':
                  return (
                    <Thinking
                      key={item.id}
                      content={item.content}
                      streaming={!item.final}
                    />
                  )
                case 'tool_call':
                  return <ToolCall key={item.id} item={item} />
                case 'final_result':
                  return <FinalResult key={item.id} item={item} />
                default:
                  return null
              }
            })}

            {localMessages.map((m) =>
              m.role === 'user' ? (
                <UserBubble key={m.id} content={m.content} />
              ) : (
                <AssistantText
                  key={m.id}
                  content={m.content}
                  streaming={!!m.streaming}
                />
              )
            )}
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
      <div className="border-t border-[var(--border-default)] bg-[var(--bg-raised)] px-5 py-3.5">
        <div
          className={`border rounded-[var(--radius-md)] overflow-hidden transition-all ${
            !canAsk
              ? 'border-[var(--border-default)] opacity-60'
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
            className="w-full bg-transparent px-4 py-2.5 text-sm text-[var(--text-primary)] placeholder-[var(--text-disabled)] resize-none outline-none disabled:cursor-not-allowed"
          />
          <div className="flex items-center justify-between px-3 py-2 bg-[var(--bg-overlay)]">
            <span className="text-xs text-[var(--text-disabled)]">
              {canAsk
                ? 'Enter 发送 · Shift+Enter 换行'
                : isTaskRunning
                ? '等待 summary.md 生成…'
                : '任务未完成'}
            </span>
            <button
              onClick={handleSend}
              disabled={!input.trim() || !canAsk}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-[var(--radius-xs)] text-xs bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer"
            >
              <Send size={11} />
              发送
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
