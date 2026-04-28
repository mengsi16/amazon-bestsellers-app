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

const TABS: { key: TabKey; label: string; emoji: string }[] = [
  { key: 'summary', label: '综合总结', emoji: '📋' },
  { key: 'marketplace', label: 'Marketplace', emoji: '🏪' },
  { key: 'reviews', label: 'Reviews', emoji: '💬' },
  { key: 'aplus', label: 'A+ Content', emoji: '🎨' },
  { key: 'fine_grained', label: 'Fine-Grained', emoji: '🔍' },
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
      <div className="flex items-center justify-center h-48 text-slate-600 text-sm">
        报告生成中，完成后将自动展示…
      </div>
    )
  }

  const currentContent = reports[activeTab]
  const currentTab = TABS.find((t) => t.key === activeTab)

  return (
    <div className="flex flex-col h-full">
      {/* Report identity hint */}
      <div className="mb-3 px-1 text-xs text-slate-600 flex flex-wrap items-center gap-2">
        <span>📁 报告已生成，可直接在线查看或下载 Markdown。</span>
        <span className="text-slate-500">类目标识：</span>
        <code className="bg-slate-800 px-1.5 py-0.5 rounded text-slate-400">
          {browseNodeId}
        </code>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 mb-4 overflow-x-auto pb-1 border-b border-slate-800">
        {availableTabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-t-lg text-sm whitespace-nowrap
                        transition-colors shrink-0 cursor-pointer
                        ${activeTab === tab.key
                          ? 'bg-slate-700 text-white border-b-2 border-indigo-500 -mb-px'
                          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                        }`}
          >
            <span>{tab.emoji}</span>
            <span>{tab.label}</span>
          </button>
        ))}

        {/* Download button */}
        {currentContent && (
          <button
            onClick={() => downloadMd(taskId, activeTab)}
            className="ml-auto shrink-0 flex items-center gap-1.5 px-3 py-2 rounded-lg
                       text-slate-500 hover:text-slate-300 hover:bg-slate-800
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
          <div className="flex items-center justify-center h-32 text-slate-600 text-sm">
            {currentTab?.label} 报告尚未生成
          </div>
        )}
      </div>
    </div>
  )
}
