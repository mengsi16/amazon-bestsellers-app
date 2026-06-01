import { useState } from 'react'
import { api, type AuthResponse, type OAuthProvider } from '../api'
import { Mail, X } from 'lucide-react'

interface Props {
  onAuth: (user: AuthResponse) => void
}

export default function AuthPage({ onAuth }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [codeSent, setCodeSent] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [sendingCode, setSendingCode] = useState(false)
  const [oauthLoading, setOauthLoading] = useState<OAuthProvider | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const data = mode === 'login'
        ? await api.emailLogin(email, password)
        : await api.emailRegister(email, password, code)
      onAuth(data)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const handleSendCode = async () => {
    setError('')
    setSendingCode(true)
    try {
      const result = await api.sendEmailCode(email, 'register')
      if (result.dev_code) {
        setCode(result.dev_code)
      }
      setCodeSent(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSendingCode(false)
    }
  }

  const handleOAuth = async (provider: OAuthProvider) => {
    setError('')
    setOauthLoading(provider)
    try {
      const authorizationUrl = await api.oauthStartUrl(provider)
      window.location.assign(authorizationUrl)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
      setOauthLoading(null)
    }
  }

  const switchMode = (nextMode: 'login' | 'register') => {
    setMode(nextMode)
    setError('')
    setCode('')
    setCodeSent(false)
  }

  return (
    <div className="min-h-screen w-full bg-[#f4f5f5] flex items-center justify-center px-4 py-8 text-[#111111]">
      <div className="relative w-full max-w-[390px] rounded-[20px] border border-[#d8d8d8] bg-white px-6 py-7 shadow-[0_22px_70px_rgba(15,23,42,0.16)] sm:px-7">
        <button
          type="button"
          aria-label="关闭"
          onClick={() => setError('请先登录或注册后进入工作台')}
          className="absolute right-5 top-5 flex h-8 w-8 items-center justify-center rounded-full text-[#111111] transition-colors hover:bg-[#f2f2f2]"
        >
          <X size={20} strokeWidth={2} />
        </button>

        <div className="mb-8 pt-7 text-center">
          <h1 className="text-[31px] font-medium leading-tight tracking-normal text-[#111111]">登录或注册</h1>
          <p className="mx-auto mt-5 max-w-[300px] text-[16px] leading-[1.55] text-[#1f1f1f]">
            保存你的任务历史、模型配置与报告上下文。
          </p>
        </div>

        <div>
          <div className="space-y-3">
            <button
              type="button"
              onClick={() => handleOAuth('google')}
              disabled={oauthLoading !== null}
              className="grid h-[52px] w-full grid-cols-[68px_1fr_68px] items-center rounded-full border border-[#d4d4d4] bg-white px-1 text-[16px] font-normal text-[#1f1f1f] transition-colors hover:bg-[#f8f8f8] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <svg className="mx-auto h-5 w-5" viewBox="0 0 48 48" aria-hidden="true">
                <path fill="#EA4335" d="M24 9.5c3.4 0 6.4 1.2 8.8 3.5l6.6-6.6C35.4 2.7 30.2.5 24 .5 14.7.5 6.7 5.8 2.8 13.6l7.8 6.1C12.4 13.7 17.8 9.5 24 9.5Z" />
                <path fill="#4285F4" d="M46.6 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h12.7c-.6 3-2.2 5.5-4.7 7.2l7.3 5.7c4.3-4 7.3-9.9 7.3-16.9Z" />
                <path fill="#FBBC05" d="M10.6 28.3A14.4 14.4 0 0 1 10.6 19.7l-7.8-6.1a23.5 23.5 0 0 0 0 20.8l7.8-6.1Z" />
                <path fill="#34A853" d="M24 47.5c6.2 0 11.4-2 15.2-5.9l-7.3-5.7c-2 1.4-4.6 2.2-7.9 2.2-6.2 0-11.5-4.2-13.4-9.8l-7.8 6.1C6.7 42.2 14.7 47.5 24 47.5Z" />
              </svg>
              <span className="text-center">{oauthLoading === 'google' ? '正在连接 Google...' : '使用 Google 账户继续'}</span>
              <span />
            </button>
            <button
              type="button"
              onClick={() => handleOAuth('github')}
              disabled={oauthLoading !== null}
              className="grid h-[52px] w-full grid-cols-[68px_1fr_68px] items-center rounded-full border border-[#d4d4d4] bg-white px-1 text-[16px] font-normal text-[#1f1f1f] transition-colors hover:bg-[#f8f8f8] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <svg
                className="mx-auto h-5 w-5 fill-[#111111]"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path d="M12 2C6.48 2 2 6.59 2 12.25c0 4.53 2.86 8.37 6.84 9.73.5.09.68-.22.68-.49 0-.24-.01-1.05-.01-1.9-2.51.47-3.16-.62-3.36-1.19-.11-.29-.6-1.19-1.03-1.43-.35-.19-.85-.66-.01-.67.79-.01 1.35.74 1.54 1.05.9 1.55 2.34 1.11 2.91.85.09-.67.35-1.11.64-1.37-2.22-.26-4.55-1.14-4.55-5.05 0-1.11.39-2.03 1.03-2.75-.1-.26-.45-1.31.1-2.71 0 0 .84-.28 2.75 1.05A9.31 9.31 0 0 1 12 7.03c.85 0 1.71.12 2.51.34 1.91-1.33 2.75-1.05 2.75-1.05.55 1.4.2 2.45.1 2.71.64.72 1.03 1.63 1.03 2.75 0 3.92-2.34 4.79-4.57 5.05.36.32.68.93.68 1.9 0 1.37-.01 2.47-.01 2.81 0 .27.18.59.69.49A10.12 10.12 0 0 0 22 12.25C22 6.59 17.52 2 12 2Z" />
              </svg>
              <span className="text-center">{oauthLoading === 'github' ? '正在连接 GitHub...' : '使用 GitHub 账户继续'}</span>
              <span />
            </button>
          </div>

          <div className="my-7 flex items-center gap-6">
            <div className="h-px flex-1 bg-[#dedede]" />
            <span className="text-[15px] text-[#1f1f1f]">或</span>
            <div className="h-px flex-1 bg-[#dedede]" />
          </div>

          <div className="mb-4 grid grid-cols-2 rounded-full border border-[#d2d2d2] bg-[#f4f4f4] p-1">
            <button
              type="button"
              onClick={() => switchMode('login')}
              className={`h-10 rounded-full text-[15px] transition-all ${
                mode === 'login'
                  ? 'bg-white text-[#111111] shadow-[0_1px_5px_rgba(0,0,0,0.12)]'
                  : 'text-[#666666] hover:text-[#111111]'
              }`}
            >
              登录
            </button>
            <button
              type="button"
              onClick={() => switchMode('register')}
              className={`h-10 rounded-full text-[15px] transition-all ${
                mode === 'register'
                  ? 'bg-white text-[#111111] shadow-[0_1px_5px_rgba(0,0,0,0.12)]'
                  : 'text-[#666666] hover:text-[#111111]'
              }`}
            >
              注册
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="sr-only">邮箱地址</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                maxLength={50}
                className="h-[56px] w-full rounded-full border border-[#3b3b3b] bg-white px-5 text-[16px] text-[#111111] outline-none transition-shadow placeholder:text-[#8b8b8b] focus:shadow-[0_0_0_3px_rgba(0,0,0,0.12)]"
                placeholder="电子邮件地址"
              />
            </div>
            <div>
              <label className="sr-only">密码</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                className="h-[56px] w-full rounded-full border border-[#3b3b3b] bg-white px-5 text-[16px] text-[#111111] outline-none transition-shadow placeholder:text-[#8b8b8b] focus:shadow-[0_0_0_3px_rgba(0,0,0,0.12)]"
                placeholder="密码（至少 6 位）"
              />
            </div>
            {mode === 'register' && (
              <div>
                <label className="sr-only">验证码</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    required
                    inputMode="numeric"
                    minLength={6}
                    maxLength={6}
                    className="h-[56px] min-w-0 flex-1 rounded-full border border-[#3b3b3b] bg-white px-5 text-[16px] text-[#111111] outline-none transition-shadow placeholder:text-[#8b8b8b] focus:shadow-[0_0_0_3px_rgba(0,0,0,0.12)]"
                    placeholder="验证码"
                  />
                  <button
                    type="button"
                    onClick={handleSendCode}
                    disabled={sendingCode || !email}
                    className="flex h-[56px] shrink-0 items-center gap-1.5 rounded-full border border-[#d4d4d4] bg-white px-4 text-[14px] text-[#111111] transition-colors hover:bg-[#f8f8f8] disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    <Mail size={13} />
                    {sendingCode ? '发送中' : codeSent ? '重发' : '发送'}
                  </button>
                </div>
              </div>
            )}

            {error && (
              <p className="rounded-[12px] border border-[#ffd0d0] bg-[#fff6f6] px-3 py-2 text-[13px] leading-relaxed text-[#b42318]">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="mt-1 flex h-[56px] w-full items-center justify-center rounded-full bg-[#0f0f0f] px-5 text-[16px] font-medium text-white transition-colors hover:bg-[#242424] disabled:cursor-not-allowed disabled:opacity-55"
            >
              {loading ? '处理中...' : mode === 'login' ? '继续' : '注册'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
