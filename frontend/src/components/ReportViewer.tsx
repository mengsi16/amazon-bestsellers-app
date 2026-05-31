import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useState } from 'react'
import { Download } from 'lucide-react'
import { api } from '../api'
import type { ReportKey, Reports } from '../api'

interface Props {
  taskId: string
  browseNodeId: string
  reports: Reports
}

type TabKey = keyof Reports

const TABS: { key: TabKey; label: string }[] = [
  { key: 'summary', label: '综合总结' },
  { key: 'marketplace', label: 'Marketplace' },
  { key: 'reviews', label: 'Reviews' },
  { key: 'aplus', label: 'A+ Content' },
  { key: 'fine_grained', label: 'Fine-Grained' },
]

function downloadMd(taskId: string, dim: ReportKey) {
  const a = document.createElement('a')
  a.href = api.getReportDownloadUrl(taskId, dim)
  a.download = ''
  a.click()
}

export default function ReportViewer({ taskId, browseNodeId, reports }: Props) {
  const availableTabs = TABS.filter((t) => reports[t.key])
  const [activeTab, setActiveTab] = useState<TabKey>(
    availableTabs.length > 0 ? availableTabs[0].key : 'summary'
  )

  if (availableTabs.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-[var(--text-tertiary)] text-sm">
        报告生成中，完成后将自动展示…
      </div>
    )
  }

  const currentContent = reports[activeTab]
  const currentTab = TABS.find((t) => t.key === activeTab)

  return (
    <div className="flex flex-col h-full">
      {/* Report identity hint */}
      <div className="mb-3 px-1 text-xs text-[var(--text-secondary)] flex flex-wrap items-center gap-2">
        <span>报告已生成，可在线查看或下载 Markdown。</span>
        <span className="text-[var(--text-tertiary)]">类目标识：</span>
        <code className="bg-[var(--bg-overlay)] px-1.5 py-0.5 rounded text-[var(--text-secondary)] border border-[var(--border-default)]">
          {browseNodeId}
        </code>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 mb-4 overflow-x-auto pb-1 border-b border-[var(--border-default)]">
        {availableTabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-t-[var(--radius-sm)] text-sm whitespace-nowrap
                        transition-colors shrink-0 cursor-pointer
                        ${activeTab === tab.key
                          ? 'bg-[var(--bg-overlay)] text-[var(--text-primary)] border-b-2 border-[var(--text-primary)] -mb-px'
                          : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-overlay)]'
                        }`}
          >
            <span>{tab.label}</span>
          </button>
        ))}

        {/* Download button */}
        {currentContent && (
          <button
            onClick={() => downloadMd(taskId, activeTab)}
            className="ml-auto shrink-0 flex items-center gap-1.5 px-3 py-2 rounded-[var(--radius-sm)]
                       text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-overlay)]
                       transition-colors text-xs cursor-pointer"
          >
            <Download size={13} />
            下载当前 .md
          </button>
        )}
      </div>

      {/* Report content */}
      <div className="flex-1 overflow-y-auto">
        {currentContent ? (
          <div className="prose max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {currentContent}
            </ReactMarkdown>
          </div>
        ) : (
          <div className="flex items-center justify-center h-32 text-[var(--text-tertiary)] text-sm">
            {currentTab?.label} 报告尚未生成
          </div>
        )}
      </div>
    </div>
  )
}
