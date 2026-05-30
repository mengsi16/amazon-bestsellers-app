import { useState } from 'react'
import { api, type AuthResponse } from '../api'
import { LogIn, UserPlus } from 'lucide-react'

interface Props {
  onAuth: (user: AuthResponse) => void
}

export default function AuthPage({ onAuth }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const fn = mode === 'login' ? api.login : api.register
      const data = await fn(username, password)
      onAuth(data)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-[var(--bg-deep)]">
      <div className="w-full max-w-sm px-6">
        <div className="text-center mb-8">
          <div className="w-14 h-14 rounded-[var(--radius-lg)] bg-[var(--accent-muted)] border border-[var(--accent)]/20 flex items-center justify-center mb-4 mx-auto">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M3 9L12 4L21 9V20H15V14H9V20H3V9Z" fill="var(--accent)" />
            </svg>
          </div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">Amazon Bestsellers</h1>
          <p className="text-sm text-[var(--text-tertiary)] mt-1">类目分析助手</p>
        </div>

        <div className="bg-[var(--bg-raised)] border border-[var(--border-default)] rounded-[var(--radius-xl)] p-6">
          <div className="flex gap-1.5 mb-5 p-1 bg-[var(--bg-overlay)] rounded-[var(--radius-sm)]">
            <button
              onClick={() => { setMode('login'); setError('') }}
              className={`flex-1 py-1.5 rounded-[var(--radius-xs)] text-sm font-medium cursor-pointer transition-all ${
                mode === 'login'
                  ? 'bg-[var(--bg-raised)] text-[var(--text-primary)] shadow-sm'
                  : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
              }`}
            >
              登录
            </button>
            <button
              onClick={() => { setMode('register'); setError('') }}
              className={`flex-1 py-1.5 rounded-[var(--radius-xs)] text-sm font-medium cursor-pointer transition-all ${
                mode === 'register'
                  ? 'bg-[var(--bg-raised)] text-[var(--text-primary)] shadow-sm'
                  : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
              }`}
            >
              注册
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs text-[var(--text-secondary)] mb-1.5">用户名</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                minLength={2}
                maxLength={50}
                className="w-full px-3 py-2 bg-[var(--bg-deep)] border border-[var(--border-default)] rounded-[var(--radius-md)] text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-glow)] transition-all placeholder:text-[var(--text-disabled)]"
                placeholder="输入用户名"
              />
            </div>
            <div>
              <label className="block text-xs text-[var(--text-secondary)] mb-1.5">密码</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                className="w-full px-3 py-2 bg-[var(--bg-deep)] border border-[var(--border-default)] rounded-[var(--radius-md)] text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-glow)] transition-all placeholder:text-[var(--text-disabled)]"
                placeholder="输入密码（至少 6 位）"
              />
            </div>

            {error && (
              <p className="text-xs text-[var(--error)] bg-[var(--error-muted)] border border-[var(--error)]/20 rounded-[var(--radius-sm)] px-3 py-2">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white rounded-[var(--radius-sm)] text-sm font-medium cursor-pointer disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
            >
              {loading ? '处理中...' : (
                <>
                  {mode === 'login' ? <LogIn size={14} /> : <UserPlus size={14} />}
                  {mode === 'login' ? '登录' : '注册'}
                </>
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}