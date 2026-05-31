import { useState } from 'react'
import { X, ExternalLink } from 'lucide-react'
import type { ModelFamily } from '../api'

interface Props {
  onSubmit: (url: string, modelFamily: ModelFamily) => void
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
  const [modelFamily, setModelFamily] = useState<ModelFamily>('sonnet')
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
    onSubmit(trimmed, modelFamily)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="relative bg-[var(--bg-raised)] border border-[var(--border-default)] rounded-[var(--radius-xl)] shadow-xl w-full max-w-lg p-6 animate-fade-in-scale">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="text-base font-semibold text-[var(--text-primary)]">新建类目分析</h2>
            <p className="text-xs text-[var(--text-tertiary)] mt-0.5">
              输入 Amazon Bestsellers 类目 URL，选择模型族后启动分析
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-[var(--radius-sm)] hover:bg-[var(--bg-overlay)] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] transition-colors cursor-pointer"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-[var(--text-primary)] mb-1.5">
              Bestsellers 类目 URL <span className="text-[var(--error)]">*</span>
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.amazon.com/gp/bestsellers/beauty/11058221/"
              className="w-full bg-[var(--bg-deep)] border border-[var(--border-default)] rounded-[var(--radius-md)] px-4 py-2.5
                         text-sm text-[var(--text-primary)] placeholder-[var(--text-disabled)]
                         focus:outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-glow)] transition-all"
              autoFocus
            />
            {error && (
              <p className="mt-1 text-xs text-[var(--error)]">{error}</p>
            )}
          </div>

          <div>
            <p className="text-xs text-[var(--text-disabled)] mb-1.5">快速选择示例：</p>
            <div className="space-y-1">
              {EXAMPLE_URLS.map((u) => (
                <button
                  key={u}
                  type="button"
                  onClick={() => setUrl(u)}
                  className="w-full text-left text-xs text-[var(--accent)] hover:text-[var(--accent-emphasis)]
                             bg-[var(--accent-muted)] hover:bg-[var(--accent)]/10 border border-[var(--accent)]/20
                             rounded-[var(--radius-sm)] px-3 py-2 truncate transition-colors cursor-pointer
                             flex items-center gap-2"
                >
                  <ExternalLink size={10} className="shrink-0" />
                  {u}
                </button>
              ))}
            </div>
          </div>

          <div>
            <span className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5 pointer-events-none">
              模型族
            </span>
            <div className="grid grid-cols-2 gap-1 rounded-[var(--radius-md)] border border-[var(--border-default)] bg-[var(--bg-deep)] p-1 relative z-10">
              {(['sonnet', 'opus'] as const).map((family) => (
                <button
                  key={family}
                  type="button"
                  onClick={() => setModelFamily(family)}
                  className={`rounded-[var(--radius-sm)] px-3 py-2 text-sm transition-colors ${
                    modelFamily === family
                      ? 'bg-[var(--bg-raised)] text-[var(--text-primary)] shadow-sm'
                      : 'text-[var(--text-secondary)] hover:bg-[var(--bg-overlay)]'
                  }`}
                >
                  {family === 'sonnet' ? 'Sonnet' : 'Opus'}
                </button>
              ))}
            </div>
          </div>

          <div className="p-3 bg-[var(--warning-muted)] border border-[var(--warning)]/20 rounded-[var(--radius-md)] text-xs text-[var(--text-secondary)]">
            分析预计需要 30–90 分钟。启动后可实时查看进度，分析完成后报告将自动展示。
          </div>

          <div className="flex gap-2.5 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2.5 rounded-[var(--radius-sm)] border border-[var(--border-default)]
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-overlay)]
                         text-sm transition-colors cursor-pointer"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 px-4 py-2.5 rounded-[var(--radius-sm)] bg-[var(--accent)] hover:bg-[var(--accent-hover)]
                         text-white text-sm font-medium transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
              {loading ? '启动中…' : '开始分析'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
