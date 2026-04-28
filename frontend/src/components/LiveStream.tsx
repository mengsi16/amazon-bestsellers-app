import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Bot, User, Send, Loader2, ChevronDown, ChevronRight,
  Wrench, Cpu, CheckCircle2, XCircle, AlertTriangle, Info, Sparkles,
} from 'lucide-react'
import type { StreamItem, Task } from '../api'

// ── Local (client-side) messages — user Q&A that interleaves with SSE items ──
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

// ── Stream-item renderers ────────────────────────────────────────────────────

function SystemNote({ content }: { content: string }) {
  return (
    <div className="flex justify-center my-2">
      <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-800/60 border border-slate-700/50 text-xs text-slate-400">
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
      <div className="shrink-0 w-7 h-7 rounded-full bg-indigo-700 flex items-center justify-center mt-1">
        <Bot size={14} className="text-white" />
      </div>
      <div className="flex-1 min-w-0 max-w-[calc(100%-3rem)]">
        <div className="prose prose-sm prose-invert max-w-none text-slate-200 leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {content || '▍'}
          </ReactMarkdown>
        </div>
        {streaming && (
          <span className="inline-block w-1 h-4 bg-indigo-400 animate-pulse ml-0.5 align-text-bottom" />
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
        className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300 cursor-pointer"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Sparkles size={11} className="text-purple-400/60" />
        Thinking {streaming && '…'}
      </button>
      {open && (
        <div className="mt-1 ml-4 pl-3 border-l border-purple-800/40 text-xs text-slate-400/80 italic whitespace-pre-wrap">
          {content || '…'}
        </div>
      )}
    </div>
  )
}

function toolIconFor(name?: string) {
  if (name === 'Task' || name === 'Agent') return <Cpu size={13} className="text-emerald-400" />
  return <Wrench size={13} className="text-amber-400" />
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
    if (isError) return <span className="flex items-center gap-0.5 text-red-400 text-[10px]"><XCircle size={10} /> error</span>
    if (isKilled) return <span className="flex items-center gap-0.5 text-amber-400 text-[10px]"><AlertTriangle size={10} /> killed</span>
    if (isDone) return <span className="flex items-center gap-0.5 text-emerald-400 text-[10px]"><CheckCircle2 size={10} /> done</span>
    return <span className="flex items-center gap-0.5 text-indigo-400 text-[10px]"><Loader2 size={10} className="animate-spin" /> running</span>
  })()

  const activities = meta.subagent_activities || []

  return (
    <div className="ml-10 my-1">
      <div
        className={`rounded-lg border ${
          isSub
            ? 'border-emerald-900/40 bg-emerald-950/20'
            : isError
            ? 'border-red-900/50 bg-red-950/20'
            : 'border-slate-800 bg-slate-900/40'
        }`}
      >
        <button
          onClick={() => setExpanded((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2 text-left cursor-pointer hover:bg-slate-800/30 rounded-lg"
        >
          {expanded ? <ChevronDown size={12} className="text-slate-500" /> : <ChevronRight size={12} className="text-slate-500" />}
          {toolIconFor(meta.tool_name)}
          <span className={`text-xs font-semibold ${isSub ? 'text-emerald-300' : 'text-amber-300'}`}>
            {meta.tool_name || 'Tool'}
          </span>
          <span className="text-xs text-slate-400 truncate flex-1 font-mono">
            {meta.input_summary || item.content || '…'}
          </span>
          {statusChip}
        </button>

        {expanded && (
          <div className="px-3 pb-3 border-t border-slate-800/60 pt-2 space-y-2">
            {meta.input_summary && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Input</p>
                <pre className="text-xs text-slate-400 bg-slate-950/60 rounded px-2 py-1.5 overflow-x-auto whitespace-pre-wrap break-all font-mono">
                  {meta.input_summary}
                </pre>
              </div>
            )}

            {isSub && activities.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
                  Sub-agent activities ({activities.length})
                </p>
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {activities.slice(-15).map((a, i) => (
                    <div key={i} className="text-xs text-slate-400 flex items-start gap-2">
                      <Loader2 size={10} className="text-emerald-500/60 shrink-0 mt-0.5" />
                      <span className="flex-1 min-w-0 break-words">
                        {a.description}
                        {typeof a.duration_ms === 'number' && a.duration_ms > 0 && (
                          <span className="text-slate-600 ml-1.5">
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
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Result</p>
                <pre className="text-xs text-slate-300 bg-slate-950/60 rounded px-2 py-1.5 overflow-x-auto whitespace-pre-wrap break-all">
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

  // Common case (success): the full answer already streamed as `assistant_text`,
  // so render a compact stats pill only — no duplicate body.
  if (!hasContent) {
    return (
      <div className="my-4 flex justify-center">
        <div
          className={`inline-flex items-center gap-3 px-4 py-1.5 rounded-full border text-xs ${
            isError
              ? 'border-red-800/60 bg-red-950/40 text-red-300'
              : 'border-emerald-800/60 bg-emerald-950/40 text-emerald-300'
          }`}
        >
          <Sparkles size={12} />
          <span className="font-medium">{isError ? '任务失败' : '任务完成'}</span>
          {dur && <span className="opacity-70">用时 {dur}</span>}
          {cost && <span className="opacity-70">成本 {cost}</span>}
          {typeof turns === 'number' && <span className="opacity-70">{turns} turns</span>}
        </div>
      </div>
    )
  }

  // Error path (or if we ever get a final_result with body content only).
  return (
    <div
      className={`my-4 rounded-2xl border overflow-hidden ${
        isError
          ? 'border-red-800/50 bg-gradient-to-br from-red-950/40 to-slate-950'
          : 'border-emerald-800/50 bg-gradient-to-br from-emerald-950/40 to-slate-950'
      }`}
    >
      <div
        className={`px-4 py-2 border-b flex items-center gap-2 ${
          isError ? 'border-red-900/40 bg-red-900/20' : 'border-emerald-900/40 bg-emerald-900/20'
        }`}
      >
        <Sparkles size={14} className={isError ? 'text-red-400' : 'text-emerald-400'} />
        <span className={`text-xs font-semibold ${isError ? 'text-red-300' : 'text-emerald-300'}`}>
          {isError ? '失败' : '最终报告'}
        </span>
        <div className={`ml-auto flex items-center gap-3 text-[10px] ${isError ? 'text-red-400/70' : 'text-emerald-400/70'}`}>
          {dur && <span>用时 {dur}</span>}
          {cost && <span>成本 {cost}</span>}
          {typeof turns === 'number' && <span>{turns} turns</span>}
        </div>
      </div>
      <div className="p-4 prose prose-sm prose-invert max-w-none text-slate-200 leading-relaxed">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>
      </div>
    </div>
  )
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex gap-3 justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm px-4 py-3 text-sm bg-indigo-600 text-white whitespace-pre-wrap">
        {content}
      </div>
      <div className="shrink-0 w-7 h-7 rounded-full bg-slate-600 flex items-center justify-center mt-1">
        <User size={14} className="text-white" />
      </div>
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

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

  // Sort SSE items by insertion order (they arrive in order, just use as-is)
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
    // If user is within 60px of the bottom, keep auto-scroll on; otherwise pause it.
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
    <div className="flex flex-col h-full bg-[#0f1117]">
      {/* Stream body */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-6 space-y-3"
      >
        {isEmpty ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-600 text-sm gap-3 px-8 text-center">
            <Bot size={36} className="opacity-30" />
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

            {/* Q&A messages interleave at the end */}
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

      {/* Auto-scroll paused notice */}
      {!autoScroll && (
        <div className="px-6 pb-1 flex justify-center">
          <button
            onClick={() => {
              setAutoScroll(true)
              bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
            }}
            className="text-[11px] text-indigo-400 hover:text-indigo-300 cursor-pointer underline"
          >
            ↓ 跳到最新
          </button>
        </div>
      )}

      {/* Input bar */}
      <div className="border-t border-slate-800 bg-[#0b0e15] px-6 py-4">
        <div
          className={`border rounded-xl overflow-hidden transition-colors ${
            !canAsk
              ? 'border-slate-800 opacity-60'
              : 'border-slate-700 focus-within:border-indigo-600'
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
            className="w-full bg-transparent px-4 py-3 text-sm text-slate-200 placeholder-slate-600 resize-none outline-none disabled:cursor-not-allowed"
          />
          <div className="flex items-center justify-between px-3 py-2 bg-slate-900/50">
            <span className="text-xs text-slate-600">
              {canAsk
                ? 'Enter 发送 · Shift+Enter 换行'
                : isTaskRunning
                ? '等待 summary.md 生成…'
                : '任务未完成'}
            </span>
            <button
              onClick={handleSend}
              disabled={!input.trim() || !canAsk}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              <Send size={12} />
              发送
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
