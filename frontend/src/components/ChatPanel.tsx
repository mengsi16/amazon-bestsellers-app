import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Send, Bot, User, Loader2 } from 'lucide-react'
import { streamChat } from '../api'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
}

interface Props {
  taskId: string
  disabled?: boolean
}

export default function ChatPanel({ taskId, disabled }: Props) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content:
        '你好！分析报告已就绪。有任何问题都可以问我，例如：\n\n- A+ 内容分析里的某个结论是什么意思？\n- Marketplace 分析中的价格区间有哪些机会点？\n- Fine-Grained 维度的细分标签如何运用到选品？',
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading || disabled) return

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
    }
    const assistantMsgId = crypto.randomUUID()
    const assistantMsg: Message = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      streaming: true,
    }

    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setInput('')
    setLoading(true)

    try {
      for await (const chunk of streamChat(taskId, text)) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: m.content + chunk }
              : m
          )
        )
      }
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId
            ? { ...m, content: `❌ 请求失败：${err}`, streaming: false }
            : m
        )
      )
    } finally {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId ? { ...m, streaming: false } : m
        )
      )
      setLoading(false)
    }
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

  return (
    <div className="flex flex-col h-full">
      <div className="mb-3 flex items-center gap-2">
        <Bot size={16} className="text-indigo-400" />
        <h2 className="text-sm font-semibold text-white">追问 Claude</h2>
        {disabled && (
          <span className="text-xs text-slate-600 ml-2">（分析完成后可追问）</span>
        )}
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-1 mb-4">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            {msg.role === 'assistant' && (
              <div className="shrink-0 w-7 h-7 rounded-full bg-indigo-700 flex items-center justify-center mt-1">
                <Bot size={14} className="text-white" />
              </div>
            )}
            <div
              className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${
                msg.role === 'user'
                  ? 'bg-indigo-600 text-white rounded-br-sm'
                  : 'bg-slate-800 text-slate-200 rounded-bl-sm'
              }`}
            >
              {msg.role === 'assistant' ? (
                <div className="prose prose-sm max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content || (msg.streaming ? '▍' : '')}
                  </ReactMarkdown>
                  {msg.streaming && msg.content && (
                    <span className="inline-block w-1 h-4 bg-indigo-400 animate-pulse ml-0.5 align-text-bottom" />
                  )}
                </div>
              ) : (
                <p className="whitespace-pre-wrap">{msg.content}</p>
              )}
            </div>
            {msg.role === 'user' && (
              <div className="shrink-0 w-7 h-7 rounded-full bg-slate-600 flex items-center justify-center mt-1">
                <User size={14} className="text-white" />
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input box */}
      <div className={`border rounded-xl overflow-hidden transition-colors ${
        disabled
          ? 'border-slate-800 opacity-50'
          : 'border-slate-700 focus-within:border-indigo-600'
      }`}>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value)
            autoResize()
          }}
          onKeyDown={handleKeyDown}
          disabled={disabled || loading}
          placeholder={
            disabled
              ? '分析完成后可在此追问…'
              : '输入问题，Enter 发送，Shift+Enter 换行'
          }
          rows={1}
          className="w-full bg-transparent px-4 py-3 text-sm text-slate-200
                     placeholder-slate-600 resize-none outline-none
                     disabled:cursor-not-allowed"
        />
        <div className="flex items-center justify-between px-3 py-2 bg-slate-900/50">
          <span className="text-xs text-slate-600">
            {loading ? (
              <span className="flex items-center gap-1.5 text-indigo-400">
                <Loader2 size={12} className="animate-spin" />
                Claude 正在思考…
              </span>
            ) : (
              'Enter 发送 · Shift+Enter 换行'
            )}
          </span>
          <button
            onClick={handleSend}
            disabled={!input.trim() || loading || disabled}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs
                       bg-indigo-600 hover:bg-indigo-500 text-white
                       disabled:opacity-40 disabled:cursor-not-allowed
                       transition-colors cursor-pointer"
          >
            <Send size={12} />
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
