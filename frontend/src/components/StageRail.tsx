import { CheckCircle2, Loader2, Circle, Lock } from 'lucide-react'
import type { Phases, StageInfo, Task, StreamItem } from '../api'
import { STAGE_KEYS } from '../api'

const DEFAULT_STAGES: StageInfo[] = [
  { key: 'crawl', label: '阶段 1 · 爬虫' },
  { key: 'chunk', label: '阶段 2 · 分块 + 审计' },
  { key: 'analyze', label: '阶段 3 · 四维度分析' },
  { key: 'summary', label: '阶段 4 · 汇总报告' },
  { key: 'qa', label: '阶段 5 · 追问（完成后解锁）' },
]

const STAGE_DESC: Record<string, string> = {
  crawl: '调用 MCP 爬取 Bestsellers 列表 + 50 商品详情页',
  chunk: '分块 Top50 HTML，提取四个维度的结构化数据，audit 审查完整性',
  analyze: '并行触发 marketplace / reviews / aplus / fine-grained 四个分析 agent',
  summary: '汇总四份维度报告，生成综合类目分析报告',
  qa: '就报告内容自由追问，Claude 会基于已生成的数据回答',
}

interface Props {
  task: Task | null
  phases: Phases
  catalog: StageInfo[] | null
  currentItem?: StreamItem | null
}

function getPhase(phases: Phases, key: string): boolean {
  return Boolean((phases as unknown as Record<string, boolean>)[key])
}

function stageStatus(
  key: string,
  phases: Phases,
  task: Task | null,
): 'done' | 'active' | 'pending' | 'locked' {
  if (getPhase(phases, key)) return 'done'
  // qa is always "locked" until summary done
  if (key === 'qa' && !phases.summary) return 'locked'

  // First non-done pipeline stage is the active one (only when task is running)
  if (task?.status === 'running') {
    for (const k of ['crawl', 'chunk', 'analyze', 'summary']) {
      if (!getPhase(phases, k)) {
        return k === key ? 'active' : 'pending'
      }
    }
  }
  return 'pending'
}

function StatusIcon({ status }: { status: 'done' | 'active' | 'pending' | 'locked' }) {
  if (status === 'done') return <CheckCircle2 size={18} className="text-emerald-400" />
  if (status === 'active') return <Loader2 size={18} className="text-indigo-400 animate-spin" />
  if (status === 'locked') return <Lock size={14} className="text-slate-700" />
  return <Circle size={18} className="text-slate-700" />
}

export default function StageRail({ task, phases, catalog, currentItem }: Props) {
  const stages = (catalog && catalog.length > 0) ? catalog : DEFAULT_STAGES

  const doneCount = STAGE_KEYS.slice(0, 4).filter((k) => getPhase(phases, k)).length
  const pct = Math.round((doneCount / 4) * 100)

  return (
    <aside className="shrink-0 w-72 border-l border-slate-800 bg-[#0b0e15] flex flex-col">
      <div className="p-4 border-b border-slate-800">
        <h2 className="text-sm font-semibold text-white">流水线进度</h2>
        <div className="mt-3 flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                task?.status === 'failed'
                  ? 'bg-red-500'
                  : phases.summary
                  ? 'bg-emerald-500'
                  : 'bg-indigo-500'
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-xs text-slate-400 tabular-nums">{pct}%</span>
        </div>
        <p className="text-xs text-slate-500 mt-1.5">
          {doneCount} / 4 阶段完成
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {stages.map((s, idx) => {
          const st = stageStatus(s.key, phases, task)
          const isActive = st === 'active'
          const isDone = st === 'done'
          const isLocked = st === 'locked'
          return (
            <div
              key={s.key}
              className={`relative rounded-lg border transition-colors ${
                isDone
                  ? 'border-emerald-900/40 bg-emerald-950/20'
                  : isActive
                  ? 'border-indigo-700/60 bg-indigo-950/40 ring-1 ring-indigo-600/30'
                  : isLocked
                  ? 'border-slate-800 bg-slate-900/30 opacity-60'
                  : 'border-slate-800 bg-slate-900/30'
              }`}
            >
              <div className="flex items-start gap-2.5 p-3">
                <div className="shrink-0 mt-0.5">
                  <StatusIcon status={st} />
                </div>
                <div className="min-w-0 flex-1">
                  <p
                    className={`text-sm font-medium leading-snug ${
                      isDone
                        ? 'text-emerald-300'
                        : isActive
                        ? 'text-indigo-200'
                        : 'text-slate-400'
                    }`}
                  >
                    {s.label}
                  </p>
                  <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                    {STAGE_DESC[s.key] || ''}
                  </p>
                  {isActive && currentItem && (
                    <div className="mt-2 text-xs text-indigo-300/80 truncate">
                      <span className="text-slate-500">当前：</span>
                      {currentItem.meta?.last_activity
                        || currentItem.meta?.input_summary
                        || currentItem.content.slice(0, 60)
                        || '…'}
                    </div>
                  )}
                </div>
              </div>

              {idx < stages.length - 1 && (
                <div
                  className={`absolute left-[21px] -bottom-2 w-px h-2 ${
                    isDone ? 'bg-emerald-800' : 'bg-slate-800'
                  }`}
                />
              )}
            </div>
          )
        })}
      </div>

      <div className="border-t border-slate-800 p-3 text-[11px] text-slate-600 leading-relaxed">
        <p>• 流水线单次运行约 30–90 分钟。</p>
        <p>• chunks 已存在时会自动跳过重复 chunker。</p>
        <p>• 中途不会停下来问你，summary.md 写出才算完成。</p>
      </div>
    </aside>
  )
}
