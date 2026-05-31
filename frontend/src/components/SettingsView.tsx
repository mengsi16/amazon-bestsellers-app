import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import {
  CheckCircle2,
  Cpu,
  KeyRound,
  Plus,
  RefreshCw,
  Server,
  ShieldCheck,
  Star,
  Trash2,
} from 'lucide-react'
import { api } from '../api'
import type { ModelConfig } from '../api'

interface SettingsViewProps {
  onClose?: () => void
  onEnterEdit?: () => void
}

interface FormState {
  name: string
  apiKey: string
  baseUrl: string
  model: string
  isDefault: boolean
}

const EMPTY_FORM: FormState = {
  name: '',
  apiKey: '',
  baseUrl: '',
  model: '',
  isDefault: true,
}

function formatDate(value: string) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

export default function SettingsView({ onClose, onEnterEdit }: SettingsViewProps) {
  const [configs, setConfigs] = useState<ModelConfig[]>([])
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [error, setError] = useState('')

  const defaultConfig = useMemo(
    () => configs.find((config) => config.is_default) ?? null,
    [configs]
  )

  const loadConfigs = async () => {
    setLoading(true)
    setError('')
    try {
      setConfigs(await api.listModelConfigs())
    } catch (err) {
      console.error('加载模型配置失败:', err)
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadConfigs()
  }, [])

  const updateForm = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const startEdit = (config: ModelConfig) => {
    onEnterEdit?.()
    setEditingId(config.id)
    setForm({
      name: config.name,
      apiKey: '',
      baseUrl: config.base_url || '',
      model: config.model || '',
      isDefault: config.is_default,
    })
    setError('')
  }

  const cancelEdit = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setError('')
  }

  const saveConfig = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    const payload = {
      name: form.name.trim(),
      apiKey: form.apiKey.trim(),
      baseUrl: form.baseUrl.trim() || undefined,
      model: form.model.trim() || undefined,
      isDefault: form.isDefault,
    }
    if (!payload.name) {
      setError('配置名称不能为空')
      return
    }

    setSaving(true)
    try {
      if (editingId) {
        await api.updateModelConfig(editingId, payload)
        setEditingId(null)
        setForm(EMPTY_FORM)
      } else {
        if (!payload.apiKey) {
          setError('API Key 不能为空')
          return
        }
        const created = await api.createModelConfig(payload)
        setConfigs((prev) => [created, ...prev.map((item) => (
          created.is_default ? { ...item, is_default: false } : item
        ))])
        setForm(EMPTY_FORM)
      }
      await loadConfigs()
    } catch (err) {
      console.error('保存模型配置失败:', err)
      setError(String(err))
    } finally {
      setSaving(false)
    }
  }

  const deleteConfig = async (config: ModelConfig) => {
    if (!confirm(`删除模型配置「${config.name}」？`)) return
    setBusyId(config.id)
    setError('')
    try {
      await api.deleteModelConfig(config.id)
      setConfigs((prev) => prev.filter((item) => item.id !== config.id))
    } catch (err) {
      console.error('删除模型配置失败:', err)
      setError(String(err))
    } finally {
      setBusyId(null)
    }
  }

  const setDefaultConfig = async (config: ModelConfig) => {
    setBusyId(config.id)
    setError('')
    try {
      await api.setDefaultModelConfig(config.id)
      setConfigs((prev) => prev.map((item) => ({
        ...item,
        is_default: item.id === config.id,
      })))
    } catch (err) {
      console.error('设置默认模型配置失败:', err)
      setError(String(err))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <main className="min-h-screen bg-[var(--bg-deep)] text-[var(--text-primary)]">
      <div className="border-b border-[var(--border-default)] bg-[var(--bg-raised)]">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-md)] border border-[var(--border-default)] bg-[var(--bg-base)] text-[var(--accent)]">
              <ShieldCheck size={18} />
            </div>
            <div>
              <h1 className="text-base font-semibold tracking-normal">设置</h1>
              <p className="text-xs text-[var(--text-tertiary)]">模型配置</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={loadConfigs}
              disabled={loading}
              className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-base)] px-3 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-overlay)] disabled:opacity-50"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
              刷新
            </button>
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                className="h-8 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-base)] px-3 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-overlay)]"
              >
                关闭
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="mx-auto grid max-w-6xl gap-5 px-6 py-6 lg:grid-cols-[390px_1fr]">
        <form
          onSubmit={saveConfig}
          className="h-fit rounded-[var(--radius-md)] border border-[var(--border-default)] bg-[var(--bg-raised)] p-5 shadow-sm"
        >
          <div className="mb-5 flex items-center gap-2">
            {editingId ? <Trash2 size={16} className="text-amber-500" /> : <Plus size={16} className="text-[var(--accent)]" />}
            <h2 className="text-sm font-semibold tracking-normal">{editingId ? '编辑配置' : '新增配置'}</h2>
            {editingId && (
              <button type="button" onClick={cancelEdit} className="ml-auto text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] underline">
                取消编辑
              </button>
            )}
          </div>

          <div className="space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-[var(--text-secondary)]">配置名称</span>
              <input
                value={form.name}
                onChange={(event) => updateForm('name', event.target.value)}
                className="w-full rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-base)] px-3 py-2 text-sm outline-none transition-all focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-glow)]"
                placeholder="公司网关"
              />
            </label>

            <label className="block">
              <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">
                <KeyRound size={12} />
                API Key
              </span>
              <input
                value={form.apiKey}
                onChange={(event) => updateForm('apiKey', event.target.value)}
                type="password"
                autoComplete="new-password"
                className="w-full rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-base)] px-3 py-2 text-sm outline-none transition-all focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-glow)]"
                placeholder="sk-ant-..."
              />
            </label>

            <label className="block">
              <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">
                <Server size={12} />
                base URL
              </span>
              <input
                value={form.baseUrl}
                onChange={(event) => updateForm('baseUrl', event.target.value)}
                className="w-full rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-base)] px-3 py-2 text-sm outline-none transition-all focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-glow)]"
                placeholder="https://api.anthropic.com"
              />
            </label>

            <label className="block">
              <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">
                <Cpu size={12} />
                模型
              </span>
              <input
                value={form.model}
                onChange={(event) => updateForm('model', event.target.value)}
                className="w-full rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-base)] px-3 py-2 text-sm outline-none transition-all focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-glow)]"
                placeholder="claude-sonnet-4-5"
              />
            </label>

            <label className="flex items-center justify-between rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-base)] px-3 py-2">
              <span className="text-xs font-medium text-[var(--text-secondary)]">设为默认</span>
              <input
                type="checkbox"
                checked={form.isDefault}
                onChange={(event) => updateForm('isDefault', event.target.checked)}
                className="h-4 w-4 accent-[var(--accent)]"
              />
            </label>
          </div>

          {error && (
            <div className="mt-4 rounded-[var(--radius-sm)] border border-[var(--error)]/20 bg-[var(--error-muted)] px-3 py-2 text-xs text-[var(--error)]">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={saving}
            className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-[var(--radius-sm)] bg-[var(--accent)] px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[var(--accent-hover)] disabled:opacity-50"
          >
            {editingId ? <Trash2 size={14} /> : <Plus size={14} />}
            {saving ? '保存中...' : editingId ? '更新配置' : '保存配置'}
          </button>
          {editingId && (
            <button
              type="button"
              onClick={cancelEdit}
              className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-[var(--radius-sm)] border border-[var(--border-default)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-overlay)] transition-colors"
            >
              取消编辑
            </button>
          )}
        </form>

        <section className="min-w-0 rounded-[var(--radius-md)] border border-[var(--border-default)] bg-[var(--bg-raised)] shadow-sm">
          <div className="flex items-center justify-between border-b border-[var(--border-default)] px-5 py-4">
            <div>
              <h2 className="text-sm font-semibold tracking-normal">配置列表</h2>
              <p className="text-xs text-[var(--text-tertiary)]">
                {defaultConfig ? `默认：${defaultConfig.name}` : '尚未设置默认配置'}
              </p>
            </div>
            <span className="rounded-full border border-[var(--border-default)] bg-[var(--bg-base)] px-2.5 py-1 text-xs text-[var(--text-tertiary)]">
              {configs.length}
            </span>
          </div>

          {loading ? (
            <div className="flex h-56 items-center justify-center text-sm text-[var(--text-tertiary)]">
              <RefreshCw size={16} className="mr-2 animate-spin" />
              加载中...
            </div>
          ) : configs.length === 0 ? (
            <div className="flex h-56 flex-col items-center justify-center gap-3 px-6 text-center">
              <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-md)] bg-[var(--accent-muted)] text-[var(--accent)]">
                <KeyRound size={18} />
              </div>
              <p className="text-sm font-medium text-[var(--text-secondary)]">暂无模型配置</p>
            </div>
          ) : (
            <div className="divide-y divide-[var(--border-default)]">
              {configs.map((config) => (
                <article key={config.id} className="px-5 py-4 transition-colors hover:bg-[var(--bg-base)]">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-semibold tracking-normal text-[var(--text-primary)]">
                          {config.name}
                        </h3>
                        {config.is_default && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-[var(--success-muted)] px-2 py-0.5 text-[11px] font-medium text-[var(--success)]">
                            <CheckCircle2 size={11} />
                            默认
                          </span>
                        )}
                        {config.has_api_key && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-[var(--accent-muted)] px-2 py-0.5 text-[11px] font-medium text-[var(--accent)]">
                            <KeyRound size={11} />
                            已保存 Key
                          </span>
                        )}
                      </div>

                      <div className="mt-3 grid gap-2 text-xs text-[var(--text-secondary)] md:grid-cols-2">
                        <div className="rounded-[var(--radius-sm)] bg-[var(--bg-base)] px-3 py-2">
                          <span className="block text-[11px] text-[var(--text-tertiary)]">模型</span>
                          <span className="block truncate font-mono">{config.model}</span>
                        </div>
                        <div className="rounded-[var(--radius-sm)] bg-[var(--bg-base)] px-3 py-2">
                          <span className="block text-[11px] text-[var(--text-tertiary)]">base URL</span>
                          <span className="block truncate">{config.base_url || '默认'}</span>
                        </div>
                      </div>

                      <p className="mt-2 text-[11px] text-[var(--text-tertiary)]">
                        {formatDate(config.created_at)}
                      </p>
                    </div>

                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        type="button"
                        onClick={() => startEdit(config)}
                        className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-raised)] px-3 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-overlay)] disabled:opacity-50"
                      >
                        <Cpu size={12} />
                        编辑
                      </button>
                      {!config.is_default && (
                        <button
                          type="button"
                          onClick={() => setDefaultConfig(config)}
                          disabled={busyId === config.id}
                          className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-raised)] px-3 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-overlay)] disabled:opacity-50"
                        >
                          <Star size={12} />
                          设默认
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => deleteConfig(config)}
                        disabled={busyId === config.id}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] border border-[var(--error)]/20 bg-[var(--error-muted)] text-[var(--error)] transition-colors hover:bg-[var(--error)]/10 disabled:opacity-50"
                        aria-label={`删除 ${config.name}`}
                        title="删除"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
