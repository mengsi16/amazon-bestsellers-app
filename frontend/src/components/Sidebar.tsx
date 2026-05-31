import { Plus, Trash2, CheckCircle, XCircle, Loader2, Clock, LogOut, Search, Filter, Settings, FolderKanban } from 'lucide-react'
import type { Task, TaskStatus, UserInfo } from '../api'

interface Props {
  tasks: Task[]
  activeId: string | null
  activeView?: 'workspace' | 'settings'
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  user?: UserInfo | null
  onLogout?: () => void
  onSettings?: () => void
  onWorkspace?: () => void
  statusFilter?: TaskStatus | ''
  keywordFilter?: string
  onStatusFilterChange?: (s: TaskStatus | '') => void
  onKeywordFilterChange?: (k: string) => void
}

const statusIcon = (status: Task['status']) => {
  switch (status) {
    case 'completed':
      return <CheckCircle size={13} className="text-[var(--success)] shrink-0" />
    case 'failed':
      return <XCircle size={13} className="text-[var(--error)] shrink-0" />
    case 'running':
      return <Loader2 size={13} className="text-[var(--accent)] shrink-0 animate-spin" />
    default:
      return <Clock size={13} className="text-[var(--text-disabled)] shrink-0" />
  }
}

const statusLabel = (status: Task['status']) => {
  const map: Record<string, string> = { completed: '已完成', failed: '失败', running: '运行中', pending: '等待中', cancelled: '已取消' }
  return map[status]
}

const statusDot = (status: Task['status']) => {
  switch (status) {
    case 'completed': return 'bg-[var(--success)]'
    case 'failed': return 'bg-[var(--error)]'
    case 'running': return 'bg-[var(--accent)] animate-pulse'
    default: return 'bg-[var(--text-disabled)]'
  }
}

export default function Sidebar({
  tasks, activeId, activeView = 'workspace', onSelect, onNew, onDelete, user, onLogout, onSettings, onWorkspace,
  statusFilter = '', keywordFilter = '', onStatusFilterChange, onKeywordFilterChange,
}: Props) {
  return (
    <aside className="app-sidebar w-64 shrink-0 bg-[var(--bg-deep)] border-r border-[var(--border-default)] flex flex-col h-screen sticky top-0">
      {/* Header */}
      <div className="px-4 py-3.5 border-b border-[var(--border-default)]">
        <div className="flex items-center gap-2.5 mb-0.5">
          <div className="w-7 h-7 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-raised)] flex items-center justify-center text-[var(--text-secondary)]">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M3 9L12 4L21 9V20H15V14H9V20H3V9Z" fill="currentColor" />
            </svg>
          </div>
          <span className="font-semibold text-[var(--text-primary)] text-sm leading-tight">
            Amazon Bestsellers
          </span>
        </div>
        <p className="text-xs text-[var(--text-tertiary)] mt-0.5 ml-[38px]">类目分析助手</p>
        {user && (
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-[var(--border-default)]">
            <span className="text-xs text-[var(--text-secondary)] truncate">{user.username}</span>
            <button
              onClick={onLogout}
              className="text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] cursor-pointer p-1 rounded-[var(--radius-xs)] hover:bg-[var(--bg-overlay)] transition-colors"
              title="退出登录"
            >
              <LogOut size={13} />
            </button>
          </div>
        )}
      </div>

      {/* New Analysis Button */}
      <div className="p-3">
        <button
          onClick={onNew}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-[var(--radius-sm)]
                     bg-[var(--bg-raised)] hover:bg-[var(--bg-overlay)] text-[var(--text-primary)] text-sm font-medium
                     transition-all duration-150 cursor-pointer border border-[var(--border-default)]"
        >
          <Plus size={14} />
          新建分析
        </button>
      </div>

      {/* Filters */}
      <div className="px-3 pb-2 space-y-2">
        <div className="relative">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-disabled)]" />
          <input
            type="text"
            value={keywordFilter}
            onChange={(e) => onKeywordFilterChange?.(e.target.value)}
            placeholder="搜索类目 ID / URL..."
            className="w-full pl-7 pr-2 py-1.5 bg-[var(--bg-deep)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-glow)] transition-all placeholder:text-[var(--text-disabled)]"
          />
        </div>
        <div className="flex items-center gap-1 flex-wrap">
          <Filter size={11} className="text-[var(--text-disabled)] shrink-0 mr-0.5" />
          {(['', 'running', 'completed', 'failed', 'cancelled'] as const).map((s) => (
            <button
              key={s}
              onClick={() => onStatusFilterChange?.(s)}
              className={`px-1.5 py-0.5 rounded-[var(--radius-xs)] text-[10px] cursor-pointer transition-all duration-150 ${
                statusFilter === s
                  ? 'bg-[var(--accent-muted)] text-[var(--accent)] font-medium border border-[var(--accent)]/20'
                  : 'bg-transparent text-[var(--text-tertiary)] hover:bg-[var(--bg-overlay)] hover:text-[var(--text-secondary)]'
              }`}
            >
              {s === '' ? '全部' : statusLabel(s)}
            </button>
          ))}
        </div>
      </div>

      {/* Task List */}
      <div className="flex-1 overflow-y-auto px-2 pb-4">
        <button
          onClick={onWorkspace}
          className={`mb-2 flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-2 text-left text-xs transition-colors ${
            activeView === 'workspace'
              ? 'bg-[var(--bg-raised)] text-[var(--text-primary)] border border-[var(--border-default)]'
              : 'text-[var(--text-secondary)] border border-transparent hover:bg-[var(--bg-overlay)]'
          }`}
        >
          <FolderKanban size={13} />
          任务工作区
        </button>
        {tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center mt-10 px-4">
            <div className="w-10 h-10 rounded-[var(--radius-md)] bg-[var(--bg-overlay)] flex items-center justify-center mb-3">
              <Search size={15} className="text-[var(--text-disabled)]" />
            </div>
            <p className="text-xs text-[var(--text-tertiary)] text-center">
              暂无分析任务
            </p>
            <p className="text-[11px] text-[var(--text-disabled)] text-center mt-1">
              点击「新建分析」开始
            </p>
          </div>
        ) : (
          <div className="space-y-0.5">
            {tasks.map((task) => (
              <div
                key={task.id}
                onClick={() => onSelect(task.id)}
                className={`
                  group relative rounded-[var(--radius-sm)] px-2.5 py-2 cursor-pointer transition-all duration-150
                  ${activeId === task.id
                    ? 'bg-[var(--accent-muted)] border border-[var(--accent)]/20'
                    : 'border border-transparent hover:bg-[var(--bg-overlay)]'}
                `}
              >
                <div className="flex items-start gap-2">
                  <div className="mt-1">
                    <div className={`w-1.5 h-1.5 rounded-full ${statusDot(task.status)}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className={`text-xs font-medium truncate leading-snug ${
                      activeId === task.id ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'
                    }`}>
                      {task.browse_node_id}
                    </p>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      {statusIcon(task.status)}
                      <span className="text-[10px] text-[var(--text-tertiary)]">
                        {statusLabel(task.status)}
                      </span>
                    </div>
                    <p className="text-[10px] text-[var(--text-disabled)] mt-0.5">
                      {new Date(task.created_at).toLocaleString('zh-CN', {
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </p>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(task.id)
                    }}
                    className="opacity-0 group-hover:opacity-100 shrink-0 p-1 rounded-[var(--radius-xs)]
                               hover:bg-[var(--error-muted)] hover:text-[var(--error)] text-[var(--text-disabled)]
                               transition-all cursor-pointer"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="border-t border-[var(--border-default)] p-2">
        <button
          onClick={onSettings}
          className={`flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-2 text-left text-xs transition-colors ${
            activeView === 'settings'
              ? 'bg-[var(--bg-raised)] text-[var(--text-primary)] border border-[var(--border-default)]'
              : 'text-[var(--text-secondary)] border border-transparent hover:bg-[var(--bg-overlay)]'
          }`}
        >
          <Settings size={13} />
          设置
        </button>
      </div>
    </aside>
  )
}
