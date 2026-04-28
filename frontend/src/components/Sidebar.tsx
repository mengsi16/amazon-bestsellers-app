import { Plus, Trash2, CheckCircle, XCircle, Loader2, Clock } from 'lucide-react'
import type { Task } from '../api'

interface Props {
  tasks: Task[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}

const statusIcon = (status: Task['status']) => {
  switch (status) {
    case 'completed':
      return <CheckCircle size={14} className="text-emerald-400 shrink-0" />
    case 'failed':
      return <XCircle size={14} className="text-red-400 shrink-0" />
    case 'running':
      return <Loader2 size={14} className="text-indigo-400 shrink-0 animate-spin" />
    default:
      return <Clock size={14} className="text-slate-500 shrink-0" />
  }
}

const statusLabel = (status: Task['status']) => {
  const map = { completed: '已完成', failed: '失败', running: '运行中', pending: '等待中' }
  return map[status]
}

export default function Sidebar({ tasks, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <aside className="w-64 shrink-0 bg-[#13151f] border-r border-slate-800 flex flex-col h-screen sticky top-0">
      {/* Header */}
      <div className="p-4 border-b border-slate-800">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xl">🛒</span>
          <span className="font-semibold text-white text-sm leading-tight">
            Amazon Bestsellers
          </span>
        </div>
        <p className="text-xs text-slate-500">类目分析助手</p>
      </div>

      {/* New Analysis Button */}
      <div className="p-3">
        <button
          onClick={onNew}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg
                     bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium
                     transition-colors cursor-pointer"
        >
          <Plus size={16} />
          新建分析
        </button>
      </div>

      {/* Task List */}
      <div className="flex-1 overflow-y-auto px-2 pb-4">
        {tasks.length === 0 ? (
          <p className="text-xs text-slate-600 text-center mt-6 px-4">
            暂无分析任务，点击"新建分析"开始
          </p>
        ) : (
          <div className="space-y-1">
            {tasks.map((task) => (
              <div
                key={task.id}
                onClick={() => onSelect(task.id)}
                className={`
                  group relative rounded-lg px-3 py-2.5 cursor-pointer transition-colors
                  ${activeId === task.id
                    ? 'bg-slate-700 text-white'
                    : 'hover:bg-slate-800 text-slate-300'}
                `}
              >
                <div className="flex items-start gap-2">
                  <div className="mt-0.5">{statusIcon(task.status)}</div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate leading-snug">
                      {task.browse_node_id}
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5 truncate">
                      {statusLabel(task.status)}
                    </p>
                    <p className="text-xs text-slate-600 mt-0.5 truncate">
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
                    className="opacity-0 group-hover:opacity-100 shrink-0 p-0.5 rounded
                               hover:bg-red-900/40 hover:text-red-400 text-slate-600
                               transition-all cursor-pointer"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
