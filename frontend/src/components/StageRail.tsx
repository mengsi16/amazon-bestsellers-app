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
  if (key === 'qa' && !phases.summary) return 'locked'

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
  if (status === 'done') return <CheckCircle2 size={15} className="text-[var(--success)]" />
  if (status === 'active') return (
    <div className="relative">
      <Loader2 size={15} className="text-[var(--accent)] animate-spin" />
    </div>
  )
  if (status === 'locked') return <Lock size={12} className="text-[var(--text-disabled)]" />
  return <Circle size={15} className="text-[var(--text-disabled)]" />
}

export default function StageRail({ task, phases, catalog, currentItem }: Props) {
  const stages = (catalog && catalog.length > 0) ? catalog : DEFAULT_STAGES

  const doneCount = STAGE_KEYS.slice(0, 4).filter((k) => getPhase(phases, k)).length
  const pct = Math.round((doneCount / 4) * 100)

  return (
    <aside className="h-full w-full shrink-0 border-l border-[var(--border-default)] bg-[var(--bg-raised)] flex flex-col">
      <div className="px-4 py-3.5 border-b border-[var(--border-default)]">
        <h2 className="text-sm font-medium text-[var(--text-primary)]">流水线进度</h2>
        <div className="mt-3 flex items-center gap-2.5">
          <div className="flex-1 h-1.5 bg-[var(--bg-overlay)] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ease-out ${
                task?.status === 'failed'
                  ? 'bg-[var(--error)]'
                  : phases.summary
                  ? 'bg-[var(--success)]'
                  : 'bg-[var(--accent)]'
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-xs text-[var(--text-tertiary)] tabular-nums font-medium min-w-[28px] text-right">{pct}%</span>
        </div>
        <p className="text-[11px] text-[var(--text-disabled)] mt-1.5">
          {doneCount} / 4 阶段完成
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
        {stages.map((s, idx) => {
          const st = stageStatus(s.key, phases, task)
          const isActive = st === 'active'
          const isDone = st === 'done'
          const isLocked = st === 'locked'
          return (
            <div
              key={s.key}
              className={`relative rounded-[var(--radius-sm)] border transition-all duration-150 ${
                isDone
                  ? 'border-transparent bg-transparent'
                  : isActive
                  ? 'border-[var(--border-default)] bg-[var(--bg-base)]'
                  : isLocked
                  ? 'border-transparent bg-transparent opacity-50'
                  : 'border-transparent bg-transparent hover:bg-[var(--bg-base)]'
              }`}
            >
              <div className="flex items-start gap-2.5 p-3">
                <div className="shrink-0 mt-0.5">
                  <StatusIcon status={st} />
                </div>
                <div className="min-w-0 flex-1">
                  <p
                    className={`text-xs font-medium leading-snug ${
                      isDone
                        ? 'text-[var(--success)]'
                        : isActive
                        ? 'text-[var(--accent)]'
                        : isLocked
                        ? 'text-[var(--text-disabled)]'
                        : 'text-[var(--text-secondary)]'
                    }`}
                  >
                    {s.label}
                  </p>
                  <p className="text-[10px] text-[var(--text-disabled)] mt-0.5 leading-relaxed">
                    {STAGE_DESC[s.key] || ''}
                  </p>
                  {isActive && currentItem && (
                    <div className="mt-1.5 text-[10px] text-[var(--accent)] truncate bg-[var(--accent-muted)] rounded-[var(--radius-xs)] px-1.5 py-1">
                      <span className="opacity-60">当前：</span>
                      {currentItem.meta?.last_activity
                        || currentItem.meta?.input_summary
                        || currentItem.content.slice(0, 50)
                        || '…'}
                    </div>
                  )}
                </div>
              </div>

              {idx < stages.length - 1 && (
                <div
                  className={`absolute left-[18px] -bottom-1.5 w-px h-1.5 ${
                    isDone ? 'bg-[var(--success)]/40' : 'bg-[var(--border-default)]'
                  }`}
                />
              )}
            </div>
          )
        })}
      </div>

      <div className="border-t border-[var(--border-default)] p-3 text-[10px] text-[var(--text-disabled)] leading-relaxed space-y-1">
        <p>• 流水线单次运行约 30–90 分钟</p>
        <p>• chunks 已存在时会自动跳过重复 chunker</p>
        <p>• 中途不会停下来问你，summary.md 写出才算完成</p>
      </div>
    </aside>
  )
}
