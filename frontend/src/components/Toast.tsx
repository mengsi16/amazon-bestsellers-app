import { useState, useCallback, useEffect, useRef } from 'react'
import { X, AlertCircle, CheckCircle, Info, AlertTriangle } from 'lucide-react'

export type ToastType = 'error' | 'success' | 'warning' | 'info'

export interface ToastItem {
  id: string
  type: ToastType
  message: string
  duration?: number
}

interface Props {
  toasts: ToastItem[]
  onDismiss: (id: string) => void
}

const iconMap = {
  error: <AlertCircle size={16} className="text-red-400 shrink-0" />,
  success: <CheckCircle size={16} className="text-emerald-400 shrink-0" />,
  warning: <AlertTriangle size={16} className="text-amber-400 shrink-0" />,
  info: <Info size={16} className="text-blue-400 shrink-0" />,
}

const bgMap = {
  error: 'border-red-800/60 bg-red-950/80',
  success: 'border-emerald-800/60 bg-emerald-950/80',
  warning: 'border-amber-800/60 bg-amber-950/80',
  info: 'border-blue-800/60 bg-blue-950/80',
}

function ToastItemView({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: string) => void }) {
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    const ms = toast.duration ?? 5000
    if (ms > 0) {
      timerRef.current = setTimeout(() => onDismiss(toast.id), ms)
    }
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [toast.id, toast.duration, onDismiss])

  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 rounded-xl border shadow-lg backdrop-blur-sm ${bgMap[toast.type]} animate-slide-in`}
    >
      <div className="mt-0.5">{iconMap[toast.type]}</div>
      <p className="text-sm text-slate-200 flex-1 min-w-0 break-words">{toast.message}</p>
      <button
        onClick={() => onDismiss(toast.id)}
        className="text-slate-500 hover:text-slate-300 cursor-pointer shrink-0 mt-0.5"
      >
        <X size={14} />
      </button>
    </div>
  )
}

export default function ToastContainer({ toasts, onDismiss }: Props) {
  if (toasts.length === 0) return null
  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 w-80 pointer-events-none">
      {toasts.map((t) => (
        <div key={t.id} className="pointer-events-auto">
          <ToastItemView toast={t} onDismiss={onDismiss} />
        </div>
      ))}
    </div>
  )
}

// Hook for managing toasts
export function useToast() {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const addToast = useCallback((type: ToastType, message: string, duration?: number) => {
    const id = crypto.randomUUID()
    setToasts((prev) => [...prev, { id, type, message, duration }])
    return id
  }, [])

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const error = useCallback((msg: string) => addToast('error', msg), [addToast])
  const success = useCallback((msg: string) => addToast('success', msg), [addToast])
  const warning = useCallback((msg: string) => addToast('warning', msg), [addToast])
  const info = useCallback((msg: string) => addToast('info', msg), [addToast])

  return { toasts, dismiss, addToast, error, success, warning, info }
}
