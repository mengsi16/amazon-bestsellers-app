import { useState } from 'react'
import { X, ExternalLink } from 'lucide-react'

interface Props {
  onSubmit: (url: string, model?: string) => void
  onClose: () => void
  loading?: boolean
}

const EXAMPLE_URLS = [
  'https://www.amazon.com/gp/bestsellers/beauty/11058221/',
  'https://www.amazon.com/gp/bestsellers/fashion/1040658/',
  'https://www.amazon.com/gp/bestsellers/home-garden/3744541/',
]

export default function NewTaskDialog({ onSubmit, onClose, loading }: Props) {
  const [url, setUrl] = useState('')
  const [model, setModel] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    const trimmed = url.trim()
    if (!trimmed) {
      setError('请输入 Amazon Bestsellers 类目 URL')
      return
    }
    if (!trimmed.includes('amazon.com') || !trimmed.includes('bestsellers')) {
      setError('URL 格式不正确，请使用完整的 Amazon Bestsellers 类目 URL')
      return
    }
    onSubmit(trimmed, model.trim() || undefined)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Dialog */}
      <div className="relative bg-[#1a1d2e] border border-slate-700 rounded-2xl
                      w-full max-w-lg shadow-2xl p-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-white">🚀 新建类目分析</h2>
            <p className="text-xs text-slate-500 mt-1">
              输入 Amazon Bestsellers 类目 URL，一键启动全自动分析
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-700 text-slate-500
                       hover:text-slate-300 transition-colors cursor-pointer"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          {/* URL input */}
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-2">
              Bestsellers 类目 URL
              <span className="text-red-400 ml-1">*</span>
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.amazon.com/gp/bestsellers/beauty/11058221/"
              className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3
                         text-sm text-slate-200 placeholder-slate-600
                         focus:outline-none focus:border-indigo-500 transition-colors"
              autoFocus
            />
            {error && (
              <p className="mt-1.5 text-xs text-red-400">{error}</p>
            )}
          </div>

          {/* Examples */}
          <div>
            <p className="text-xs text-slate-600 mb-2">快速选择示例：</p>
            <div className="space-y-1">
              {EXAMPLE_URLS.map((u) => (
                <button
                  key={u}
                  type="button"
                  onClick={() => setUrl(u)}
                  className="w-full text-left text-xs text-indigo-400 hover:text-indigo-300
                             bg-indigo-950/30 hover:bg-indigo-950/50 border border-indigo-900/50
                             rounded-lg px-3 py-2 truncate transition-colors cursor-pointer
                             flex items-center gap-2"
                >
                  <ExternalLink size={11} className="shrink-0" />
                  {u}
                </button>
              ))}
            </div>
          </div>

          {/* Optional model */}
          <div>
            <label className="block text-sm font-medium text-slate-400 mb-2">
              模型（可选）
            </label>
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="claude-sonnet-4-5（留空使用默认模型）"
              className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3
                         text-sm text-slate-200 placeholder-slate-600
                         focus:outline-none focus:border-indigo-500 transition-colors"
            />
          </div>

          {/* Info */}
          <div className="p-3 bg-amber-950/30 border border-amber-800/40 rounded-xl text-xs text-amber-300/80">
            ⏱️ 分析预计需要 30–90 分钟。启动后可实时查看进度，分析完成后报告将自动展示。
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2.5 rounded-xl border border-slate-700
                         text-slate-400 hover:text-slate-200 hover:bg-slate-800
                         text-sm transition-colors cursor-pointer"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500
                         text-white text-sm font-medium transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
              {loading ? '启动中…' : '🚀 开始分析'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
