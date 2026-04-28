import { CheckCircle2, Circle, Loader2, ChevronDown, ChevronRight, Terminal, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import type { Phases, Task } from '../api'

interface Props {
  task: Task
  phases: Phases
  logs: string[]
  onResume?: () => void
  resumeLoading?: boolean
}

const PHASE_DEFS: { key: keyof Phases; label: string; desc: string; emoji: string }[] = [
  { key: 'crawl', label: 'Phase 1: CRAWL', desc: '爬取类目列表 & 商品详情页', emoji: '🕷️' },
  { key: 'chunk', label: 'Phase 2: CHUNK', desc: '数据分块提取 + Audit 审查', emoji: '✂️' },
  { key: 'analyze', label: 'Phase 3: ANALYZE', desc: '四维度并行分析', emoji: '🔬' },
  { key: 'summary', label: 'Phase 4: SUMMARY', desc: '汇总综合报告', emoji: '📋' },
]

function PhaseRow({
  label,
  desc,
  emoji,
  done,
  active,
}: {
  label: string
  desc: string
  emoji: string
  done: boolean
  active: boolean
}) {
  return (
    <div className={`flex items-center gap-3 p-3 rounded-lg border transition-colors ${
      done
        ? 'border-emerald-800/50 bg-emerald-950/30'
        : active
        ? 'border-indigo-700/50 bg-indigo-950/30'
        : 'border-slate-800 bg-slate-900/30'
    }`}>
      <span className="text-xl w-7 text-center">{emoji}</span>
      <div className="flex-1 min-w-0">
        <p className={`text-sm font-medium ${done ? 'text-emerald-400' : active ? 'text-indigo-300' : 'text-slate-500'}`}>
          {label}
        </p>
        <p className="text-xs text-slate-600 mt-0.5">{desc}</p>
      </div>
      <div className="shrink-0">
        {done ? (
          <CheckCircle2 size={18} className="text-emerald-400" />
        ) : active ? (
          <Loader2 size={18} className="text-indigo-400 animate-spin" />
        ) : (
          <Circle size={18} className="text-slate-700" />
        )}
      </div>
    </div>
  )
}

export default function ProgressPanel({ task, phases, logs, onResume, resumeLoading = false }: Props) {
  const [showLogs, setShowLogs] = useState(false)

  const doneCount = Object.values(phases).filter(Boolean).length
  const totalPhases = PHASE_DEFS.length
  const isActuallyComplete = phases.summary
  const isIncompleteExit = task.status === 'completed' && !isActuallyComplete

  // Determine which phase is currently active (first incomplete after the last done)
  let activePhaseKey: string | null = null
  if (task.status === 'running') {
    for (const p of PHASE_DEFS) {
      if (!phases[p.key]) {
        activePhaseKey = p.key
        break
      }
    }
  }

  const progressPct = isActuallyComplete ? 100 : Math.round((doneCount / totalPhases) * 100)

  return (
    <div className="space-y-4">
      {/* Header status */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-white">分析进度</h2>
          <p className="text-xs text-slate-500 mt-0.5 break-all">{task.url}</p>
        </div>
        <div className="text-right">
          <p className="text-2xl font-bold text-indigo-400">{progressPct}%</p>
          <p className="text-xs text-slate-500">
            {doneCount}/{totalPhases} 阶段
          </p>
        </div>
      </div>

      {/* Progress bar */}
      <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            task.status === 'failed' || isIncompleteExit
              ? 'bg-red-500'
              : isActuallyComplete
              ? 'bg-emerald-500'
              : 'bg-indigo-500'
          }`}
          style={{ width: `${progressPct}%` }}
        />
      </div>

      {/* Status badge */}
      {isActuallyComplete && (
        <div className="p-3 rounded-lg bg-emerald-950/50 border border-emerald-800/50 text-emerald-300 text-sm">
          ✅ 分析完成！你可以在「报告」标签中查看内容，或直接下载 Markdown 文件。
          <code className="ml-1 text-xs bg-slate-800 px-2 py-0.5 rounded">
            类目标识：{task.browse_node_id}
          </code>
        </div>
      )}
      {isIncompleteExit && (
        <div className="p-3 rounded-lg bg-amber-950/40 border border-amber-800/50 text-amber-200 text-sm">
          ⚠️ Agent 已退出，但最终汇总报告尚未生成。当前仅完成了 {doneCount}/{totalPhases} 个阶段。
        </div>
      )}
      {task.status === 'failed' && (
        <div className="p-3 rounded-lg bg-red-950/50 border border-red-800/50 text-red-300 text-sm">
          ❌ 分析失败：{task.error || '未知错误'}
        </div>
      )}
      {task.status === 'running' && (
        <div className="p-3 rounded-lg bg-indigo-950/30 border border-indigo-800/40 text-indigo-300 text-sm flex items-center gap-2">
          <Loader2 size={14} className="animate-spin shrink-0" />
          <span>分析进行中，预计需要 30–90 分钟，请耐心等待…</span>
        </div>
      )}

      {(task.status === 'failed' || isIncompleteExit) && onResume && (
        <div className="flex justify-start">
          <button
            onClick={onResume}
            disabled={resumeLoading}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-60 disabled:cursor-not-allowed text-slate-200 text-sm transition-colors cursor-pointer"
          >
            {resumeLoading ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
            {resumeLoading ? '继续分析中…' : '继续分析'}
          </button>
        </div>
      )}

      {/* Phase steps */}
      <div className="space-y-2">
        {PHASE_DEFS.map((p) => (
          <PhaseRow
            key={p.key}
            label={p.label}
            desc={p.desc}
            emoji={p.emoji}
            done={phases[p.key]}
            active={activePhaseKey === p.key}
          />
        ))}
      </div>

      {/* Log toggle */}
      <div className="border border-slate-800 rounded-lg overflow-hidden">
        <button
          onClick={() => setShowLogs((v) => !v)}
          className="w-full flex items-center justify-between px-3 py-2.5
                     text-slate-400 hover:text-slate-200 hover:bg-slate-800/50
                     transition-colors text-sm cursor-pointer"
        >
          <span className="flex items-center gap-2">
            <Terminal size={14} />
            实时日志
            <span className="text-xs text-slate-600">({logs.length} 行)</span>
          </span>
          {showLogs ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        {showLogs && (
          <div className="bg-[#0a0d14] border-t border-slate-800 p-3 max-h-64 overflow-y-auto">
            {logs.length === 0 ? (
              <p className="text-xs text-slate-600 italic">等待日志输出…</p>
            ) : (
              <div className="space-y-0.5">
                {logs.slice(-200).map((line, i) => (
                  <p
                    key={i}
                    className={`text-xs font-mono leading-relaxed break-all ${
                      line.startsWith('[SYSTEM]')
                        ? 'text-indigo-400'
                        : line.includes('❌') || line.includes('ERROR')
                        ? 'text-red-400'
                        : line.includes('✅') || line.includes('done')
                        ? 'text-emerald-400'
                        : 'text-slate-500'
                    }`}
                  >
                    {line || '\u00a0'}
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
