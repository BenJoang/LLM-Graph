import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  Archive,
  ArchiveRestore,
  ArrowDown,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleStop,
  Copy,
  Folder,
  Gauge,
  MessageSquarePlus,
  Monitor,
  Moon,
  PanelLeftClose,
  Pencil,
  Search,
  Send,
  Settings,
  Sparkles,
  Sun,
  Trash2,
  Wrench,
  X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  cancelRun,
  createSession,
  deleteSession,
  getProfiles,
  getSession,
  getSessions,
  streamRun,
  updateSession,
  validateGraphEntrypoint,
} from './api'
import type { LiveTimelinePart, Message, Profile, Session, SessionDetail } from './types'
import {
  applyDocumentTheme,
  normalizeThemePreference,
  resolveTheme,
  type ThemePreference,
} from './theme'
import { isNearBottom } from './scroll'
import { buildConversationTurns, reduceLiveTimeline, summarizeToolArgs } from './conversation'
import {
  CONTEXT_PRESETS,
  formatContextLimit,
  normalizeSessionTitle,
  validateSessionLimits,
  type SessionLimits,
} from './session-config'
import {
  idleRunState,
  messagesWithOptimistic,
  updateSessionRunState,
  type SessionRunStates,
} from './run-state'

const DEFAULT_GRAPH_ENTRYPOINT = 'src.graphs.tool_agent_graph:astream_tool_agent'

interface Defaults {
  profile_name: string
  vision_profile_name: string
  working_dir: string
  context_window_tokens: number
  recursion_limit: number
  graph_entrypoint: string
  theme: ThemePreference
}

const DEFAULTS_KEY = 'llm-graph-gui-defaults'

function readDefaults(): Partial<Defaults> {
  try {
    const stored = JSON.parse(localStorage.getItem(DEFAULTS_KEY) || '{}')
    return { ...stored, theme: normalizeThemePreference(stored.theme) }
  } catch {
    return {}
  }
}

function formatTime(value: string) {
  const date = new Date(value)
  const today = new Date()
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function isAbortError(cause: unknown): boolean {
  return cause instanceof Error && cause.name === 'AbortError'
}

function sessionGroup(value: string) {
  const date = new Date(value)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (date.toDateString() === today.toDateString()) return '今天'
  if (date.toDateString() === yesterday.toDateString()) return '昨天'
  return '更早'
}

function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

function CopyAction({ text, label = '复制' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
  }, [])

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1400)
    } catch {
      // Clipboard may be unavailable when the window is not focused.
    }
  }

  return (
    <button className="message-action" type="button" title={copied ? '已复制' : label} onClick={() => void copy()}>
      {copied ? <Check size={13} /> : <Copy size={13} />}
      <span>{copied ? '已复制' : label}</span>
    </button>
  )
}

function RenameInput({
  value,
  saving,
  className,
  onChange,
  onCommit,
  onCancel,
}: {
  value: string
  saving: boolean
  className: string
  onChange(value: string): void
  onCommit(): void
  onCancel(): void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const cancelled = useRef(false)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  useEffect(() => {
    if (!saving) inputRef.current?.focus()
  }, [saving])

  return (
    <input
      ref={inputRef}
      className={className}
      value={value}
      maxLength={120}
      disabled={saving}
      aria-label="会话标题"
      onClick={(event) => event.stopPropagation()}
      onChange={(event) => onChange(event.target.value)}
      onBlur={() => {
        if (cancelled.current) {
          cancelled.current = false
          return
        }
        onCommit()
      }}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.key === 'Enter') {
          event.preventDefault()
          onCommit()
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          cancelled.current = true
          onCancel()
        }
      }}
    />
  )
}

function ToolCard({
  name,
  args,
  content,
  status,
  duration,
}: {
  name: string
  args?: Record<string, unknown>
  content?: string
  status?: string | null
  duration?: number | null
}) {
  const [open, setOpen] = useState(false)
  const pending = content === undefined || status === 'running' || status === 'pending'
  const failed = status === 'error'
  return (
    <div className={`tool-card ${failed ? 'tool-error' : ''} ${pending ? 'tool-running' : ''}`}>
      <button className="tool-summary" type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className="tool-chevron">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
        <span className="tool-icon"><Wrench size={13} /></span>
        <strong>{name}</strong>
        <span className="tool-separator" />
        <span className="tool-args">{summarizeToolArgs(args)}</span>
        <span className={`tool-status ${pending ? 'pending' : failed ? 'failed' : ''}`}>
          <span className="tool-state-dot" />
          {pending ? '运行中' : failed ? '失败' : '完成'}
          {duration != null ? ` ${duration.toFixed(1)}s` : ''}
        </span>
      </button>
      {open && (
        <div className="tool-detail">
          <div className="tool-io-card">
            {args && Object.keys(args).length > 0 && (
              <section><label>IN</label><pre>{JSON.stringify(args, null, 2)}</pre></section>
            )}
            {(content !== undefined || pending) && (
              <section><label>OUT</label><pre className={failed ? 'error-output' : ''}>{pending ? '等待工具返回…' : content || '（无输出）'}</pre></section>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function LiveRun({ parts, stopping }: { parts: LiveTimelinePart[]; stopping: boolean }) {
  return (
    <div className="live-run">
      <div className="live-heading"><span className="live-pulse" /><strong>{stopping ? '正在终止当前任务…' : 'Agent 正在工作'}</strong></div>
      {parts.map((part) => part.type === 'step'
        ? <div className="live-step" key={part.key}><Sparkles size={13} /><Markdown>{part.content}</Markdown></div>
        : <ToolCard key={part.key} name={part.name} args={part.args} content={part.content} status={part.status} duration={part.duration} />)}
    </div>
  )
}

function MessageTimeline({
  messages,
  running,
  stopping,
  liveTimeline,
}: {
  messages: Message[]
  running: boolean
  stopping: boolean
  liveTimeline: LiveTimelinePart[]
}) {
  const turns = useMemo(() => buildConversationTurns(messages), [messages])
  return <div className="timeline">{turns.map((turn, turnIndex) => (
    <section className="conversation-turn" key={turn.key}>
      {turn.user?.content && (
        <div className="user-message-row">
          <div className="user-message-stack">
            <div className="user-bubble"><Markdown>{turn.user.content}</Markdown></div>
            <div className="message-actions"><CopyAction text={turn.user.content} /></div>
          </div>
        </div>
      )}
      <div className="turn-response">
        {turn.parts.map((part) => {
          if (part.type === 'tool') {
            return <ToolCard key={part.key} name={part.name} args={part.args} content={part.content} status={part.status} />
          }
          if (part.type === 'execution') {
            return <div className="execution-note" key={part.key}><Sparkles size={13} /><Markdown>{part.content}</Markdown></div>
          }
          return (
            <article className="assistant-answer" key={part.key}>
              <Markdown>{part.content}</Markdown>
              <div className="message-actions"><CopyAction text={part.content} label="复制回答" /></div>
            </article>
          )
        })}
        {running && turnIndex === turns.length - 1 && <LiveRun parts={liveTimeline} stopping={stopping} />}
      </div>
    </section>
  ))}</div>
}

function SessionLimitsMenu({
  session,
  saving,
  onSave,
}: {
  session: Session
  saving: boolean
  onSave(value: SessionLimits): Promise<void>
}) {
  const [draft, setDraft] = useState({
    contextWindow: String(session.context_window_tokens),
    recursionLimit: String(session.recursion_limit),
  })
  const [validationError, setValidationError] = useState<string | null>(null)

  async function save() {
    const result = validateSessionLimits(draft)
    if (!result.ok) {
      setValidationError(result.error)
      return
    }
    setValidationError(null)
    try {
      await onSave(result.value)
    } catch (cause) {
      setValidationError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  return (
    <div className="limits-popover" role="dialog" aria-label="当前会话运行参数">
      <div className="limits-heading"><div><span>当前会话</span><strong>运行限制</strong></div><Gauge size={17} /></div>
      <label>上下文窗口</label>
      <div className="context-presets">
        {CONTEXT_PRESETS.map((preset) => <button key={preset} type="button" className={Number(draft.contextWindow) === preset ? 'active' : ''} onClick={() => setDraft({ ...draft, contextWindow: String(preset) })}>{formatContextLimit(preset)}</button>)}
      </div>
      <div className="limits-field"><input aria-label="自定义上下文窗口" type="number" min={1024} max={2_000_000} value={draft.contextWindow} onChange={(event) => setDraft({ ...draft, contextWindow: event.target.value })} /><span>tokens</span></div>
      <label htmlFor="recursion-limit">递归上限</label>
      <div className="limits-field"><input id="recursion-limit" type="number" min={1} max={1000} value={draft.recursionLimit} onChange={(event) => setDraft({ ...draft, recursionLimit: event.target.value })} /><span>steps</span></div>
      {validationError && <p className="limits-error">{validationError}</p>}
      <p className="limits-note">保存后从下一轮对话开始生效，不会修改新会话默认值。</p>
      <button className="limits-save" type="button" disabled={saving} onClick={() => void save()}>{saving ? '正在保存…' : '应用到当前会话'}</button>
    </div>
  )
}

function SettingsDialog({
  profiles,
  initial,
  onClose,
  onSave,
}: {
  profiles: Profile[]
  initial: Defaults
  onClose(): void
  onSave(value: Defaults): Promise<void>
}) {
  const [value, setValue] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  async function save() {
    setSaving(true)
    setValidationError(null)
    try {
      await onSave(value)
    } catch (cause) {
      setValidationError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="settings-dialog" onMouseDown={(event) => event.stopPropagation()}>
        <header><div><span className="eyebrow">偏好设置</span><h2>新会话默认值</h2></div><button className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <p className="settings-copy">只保存 Profile 名称和运行参数。API Key 仍由项目的 .env 管理。</p>
        <div className="settings-grid">
          <label>主模型<select value={value.profile_name} onChange={(event) => setValue({ ...value, profile_name: event.target.value })}>{profiles.map((profile) => <option key={profile.name}>{profile.name}</option>)}</select></label>
          <label>视觉模型<select value={value.vision_profile_name} onChange={(event) => setValue({ ...value, vision_profile_name: event.target.value })}>{profiles.map((profile) => <option key={profile.name}>{profile.name}</option>)}</select></label>
          <label className="wide">默认工作目录<div className="directory-field"><input value={value.working_dir} readOnly /><button onClick={async () => { const path = await window.llmGraph.selectDirectory(); if (path) setValue({ ...value, working_dir: path }) }}>选择</button></div></label>
          <label className="wide">默认 Graph 入口<input value={value.graph_entrypoint} spellCheck={false} onChange={(event) => setValue({ ...value, graph_entrypoint: event.target.value })} /></label>
          <label>上下文窗口<input type="number" min={1024} value={value.context_window_tokens} onChange={(event) => setValue({ ...value, context_window_tokens: Number(event.target.value) })} /></label>
          <label>递归上限<input type="number" min={1} max={1000} value={value.recursion_limit} onChange={(event) => setValue({ ...value, recursion_limit: Number(event.target.value) })} /></label>
          <label className="wide">外观<div className="theme-options">
            {([
              ['light', '浅色', Sun],
              ['dark', '深色', Moon],
              ['system', '跟随系统', Monitor],
            ] as const).map(([id, label, Icon]) => <button key={id} type="button" className={value.theme === id ? 'active' : ''} onClick={() => setValue({ ...value, theme: id })}><Icon size={15} />{label}</button>)}
          </div></label>
          {validationError && <p className="settings-error">{validationError}</p>}
        </div>
        <footer><button className="secondary-button" disabled={saving} onClick={onClose}>取消</button><button className="primary-button" disabled={saving} onClick={() => void save()}>{saving ? '正在验证…' : '保存默认值'}</button></footer>
      </div>
    </div>
  )
}

function DeleteDialog({
  session,
  deleting,
  error,
  onClose,
  onConfirm,
}: {
  session: Session
  deleting: boolean
  error: string | null
  onClose(): void
  onConfirm(): Promise<void>
}) {
  return (
    <div className="modal-backdrop" onMouseDown={deleting ? undefined : onClose}>
      <div className="settings-dialog delete-dialog" onMouseDown={(event) => event.stopPropagation()}>
        <header><div><span className="eyebrow danger">永久删除</span><h2>删除“{session.title}”？</h2></div><button className="icon-button" disabled={deleting} onClick={onClose}><X size={18} /></button></header>
        <p className="settings-copy">会话配置、全部消息和 Graph checkpoint 都会永久删除，此操作无法恢复。</p>
        {error && <p className="delete-error">{error}</p>}
        <footer><button className="secondary-button" disabled={deleting} onClick={onClose}>取消</button><button className="danger-button" disabled={deleting} onClick={() => void onConfirm()}>{deleting ? '正在删除…' : '永久删除'}</button></footer>
      </div>
    </div>
  )
}

export default function App() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [query, setQuery] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [runStates, setRunStates] = useState<SessionRunStates>({})
  const [error, setError] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [showArchived, setShowArchived] = useState(false)
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null)
  const [renameLocation, setRenameLocation] = useState<'sidebar' | 'header' | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [renameSaving, setRenameSaving] = useState(false)
  const [limitsOpen, setLimitsOpen] = useState(false)
  const [limitsSaving, setLimitsSaving] = useState(false)
  const [graphDraft, setGraphDraft] = useState('')
  const [graphSaving, setGraphSaving] = useState(false)
  const [deleteCandidate, setDeleteCandidate] = useState<Session | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [savedDefaults, setSavedDefaults] = useState<Partial<Defaults>>(() => readDefaults())
  const [themePreference, setThemePreference] = useState<ThemePreference>(() => normalizeThemePreference(readDefaults().theme))
  const [atBottom, setAtBottom] = useState(true)
  const conversationRef = useRef<HTMLDivElement>(null)
  const conversationInnerRef = useRef<HTMLDivElement>(null)
  const limitsMenuRef = useRef<HTMLDivElement>(null)
  const renameSavingRef = useRef(false)
  const limitsSavingRef = useRef(false)
  const followBottomRef = useRef(true)
  const runAbortRef = useRef(new Map<string, AbortController>())
  const liveSequenceRef = useRef(new Map<string, number>())
  const selectedIdRef = useRef<string | null>(null)
  const showArchivedRef = useRef(false)
  const selectionSequenceRef = useRef(0)
  const graphSavingRef = useRef(false)
  const booted = useRef(false)

  useEffect(() => {
    return () => runAbortRef.current.forEach((controller) => controller.abort())
  }, [])

  const defaults: Defaults = useMemo(() => {
    const saved = savedDefaults
    return {
      profile_name: saved.profile_name || profiles[0]?.name || 'qwen3.8-flash',
      vision_profile_name: saved.vision_profile_name || (profiles.some((item) => item.name === 'qwen3.8') ? 'qwen3.8' : profiles[0]?.name || 'qwen3-vl'),
      working_dir: saved.working_dir || detail?.session.working_dir || '',
      context_window_tokens: saved.context_window_tokens || 65536,
      recursion_limit: saved.recursion_limit || 1000,
      graph_entrypoint: saved.graph_entrypoint || DEFAULT_GRAPH_ENTRYPOINT,
      theme: themePreference,
    }
  }, [profiles, detail?.session.working_dir, savedDefaults, themePreference])

  const currentRun = selectedId ? runStates[selectedId] || idleRunState() : idleRunState()
  const running = currentRun.status !== 'idle'
  const stopping = currentRun.status === 'stopping'
  const draft = selectedId ? drafts[selectedId] || '' : ''
  const liveTimeline = currentRun.liveTimeline
  const currentError = currentRun.error || error
  const displayMessages = detail
    ? messagesWithOptimistic(detail.messages, currentRun.optimisticMessage)
    : []
  const graphLocked = Boolean(detail && (detail.messages.length > 0 || running))

  function setCurrentDraft(value: string) {
    if (!selectedId) return
    setDrafts((items) => ({ ...items, [selectedId]: value }))
  }

  function isSessionRunning(sessionId: string) {
    const status = runStates[sessionId]?.status
    return status === 'running' || status === 'stopping'
  }

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => applyDocumentTheme(resolveTheme(themePreference, media.matches))
    apply()
    void window.llmGraph.setNativeTheme(themePreference)
    if (themePreference !== 'system') return
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [themePreference])

  useEffect(() => {
    if (!limitsOpen) return
    const closeOnPointer = (event: MouseEvent) => {
      if (!limitsMenuRef.current?.contains(event.target as Node)) setLimitsOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setLimitsOpen(false)
    }
    document.addEventListener('mousedown', closeOnPointer)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnPointer)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [limitsOpen])

  useEffect(() => {
    setLimitsOpen(false)
    setEditingSessionId(null)
    setRenameLocation(null)
    setGraphDraft(detail?.session.id === selectedId ? detail.session.graph_entrypoint : '')
  }, [selectedId])

  useEffect(() => {
    if (detail?.session.id === selectedId) {
      setGraphDraft(detail.session.graph_entrypoint)
    }
  }, [detail?.session.id, detail?.session.graph_entrypoint, selectedId])

  useEffect(() => {
    selectedIdRef.current = selectedId
  }, [selectedId])

  useEffect(() => {
    showArchivedRef.current = showArchived
  }, [showArchived])

  async function refreshCompletedSession(sessionId: string) {
    const [items, updatedDetail] = await Promise.all([
      getSessions(showArchivedRef.current),
      getSession(sessionId),
    ])
    setSessions(items)
    if (selectedIdRef.current === sessionId) setDetail(updatedDetail)
  }

  async function addSession() {
    const session = await createSession(defaults)
    setShowArchived(false)
    showArchivedRef.current = false
    setSessions((items) => [session, ...items])
    selectedIdRef.current = session.id
    setSelectedId(session.id)
    setDetail({ session, messages: [] })
    followBottomRef.current = true
    setAtBottom(true)
    setDrafts((items) => ({ ...items, [session.id]: '' }))
  }

  useEffect(() => {
    if (booted.current) return
    booted.current = true
    void (async () => {
      try {
        const loadedProfiles = await getProfiles()
        setProfiles(loadedProfiles)
        const loadedSessions = await getSessions()
        if (loadedSessions.length === 0) {
          const saved = readDefaults()
          const session = await createSession({
            ...saved,
            profile_name: saved.profile_name || (loadedProfiles.some((item) => item.name === 'qwen3.8-flash') ? 'deepseekv4-flash' : loadedProfiles[0]?.name),
            vision_profile_name: saved.vision_profile_name || (loadedProfiles.some((item) => item.name === 'qwen3.8') ? 'qwen3.8' : loadedProfiles[0]?.name),
          })
          setSessions([session])
          selectedIdRef.current = session.id
          setSelectedId(session.id)
          setDetail({ session, messages: [] })
          return
        }
        setSessions(loadedSessions)
        selectedIdRef.current = loadedSessions[0].id
        setSelectedId(loadedSessions[0].id)
        setDetail(await getSession(loadedSessions[0].id))
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    })()
  }, [])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'n') {
        event.preventDefault()
        void addSession()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [defaults])

  function scrollToBottom() {
    const element = conversationRef.current
    if (!element) return
    followBottomRef.current = true
    element.scrollTop = element.scrollHeight
    setAtBottom(true)
  }

  function handleConversationScroll() {
    const element = conversationRef.current
    if (!element) return
    const pinned = isNearBottom(element)
    followBottomRef.current = pinned
    setAtBottom(pinned)
  }

  useLayoutEffect(() => {
    if (followBottomRef.current) scrollToBottom()
  }, [detail?.messages, liveTimeline])

  useLayoutEffect(() => {
    if (!detail?.session.id) return
    followBottomRef.current = true
    setAtBottom(true)
    const frame = requestAnimationFrame(scrollToBottom)
    return () => cancelAnimationFrame(frame)
  }, [detail?.session.id])

  useEffect(() => {
    const content = conversationInnerRef.current
    if (!content || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (followBottomRef.current) scrollToBottom()
    })
    observer.observe(content)
    return () => observer.disconnect()
  }, [detail?.session.id])

  const filtered = sessions.filter((session) => session.title.toLowerCase().includes(query.toLowerCase()))
  const grouped = ['今天', '昨天', '更早'].map((label) => ({
    label,
    items: filtered.filter((session) => sessionGroup(session.updated_at) === label),
  })).filter((group) => group.items.length)

  async function selectSession(id: string) {
    if (id === selectedIdRef.current) return
    const sequence = ++selectionSequenceRef.current
    selectedIdRef.current = id
    setSelectedId(id)
    setDetail(null)
    setError(null)
    followBottomRef.current = true
    setAtBottom(true)
    const loaded = await getSession(id)
    if (sequence === selectionSequenceRef.current && selectedIdRef.current === id) {
      setDetail(loaded)
    }
  }

  async function patchCurrent(patch: Partial<Session>) {
    if (!detail) return
    const session = await updateSession(detail.session.id, patch)
    setDetail((current) => current?.session.id === session.id ? { ...current, session } : current)
    setSessions((items) => items.map((item) => item.id === session.id ? session : item))
    return session
  }

  async function submit() {
    if (!detail || running || limitsSavingRef.current || !draft.trim()) return
    const sessionId = detail.session.id
    const question = draft.trim()
    setDrafts((items) => ({ ...items, [sessionId]: '' }))
    setError(null)
    setRunStates((states) => updateSessionRunState(states, sessionId, () => ({
      status: 'running',
      runId: null,
      liveTimeline: [],
      optimisticMessage: {
        id: `optimistic-${Date.now()}`,
        role: 'user',
        content: question,
        tool_calls: [],
      },
      error: null,
    })))
    liveSequenceRef.current.set(sessionId, 0)
    setLimitsOpen(false)
    followBottomRef.current = true
    setAtBottom(true)
    const abortController = new AbortController()
    runAbortRef.current.set(sessionId, abortController)
    try {
      await streamRun(sessionId, question, ({ type, data }) => {
        if (type === 'run.started') {
          setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, runId: String(data.run_id) })))
        }
        if (type === 'assistant.step') {
          const message = data.message as unknown as Message
          const sequence = (liveSequenceRef.current.get(sessionId) || 0) + 1
          liveSequenceRef.current.set(sessionId, sequence)
          setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, liveTimeline: reduceLiveTimeline(state.liveTimeline, {
            type: 'assistant.step', key: `step-${sequence}`, content: message.content || '',
          }) })))
        }
        if (type === 'tool.started') {
          const sequence = (liveSequenceRef.current.get(sessionId) || 0) + 1
          liveSequenceRef.current.set(sessionId, sequence)
          setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, liveTimeline: reduceLiveTimeline(state.liveTimeline, {
            type: 'tool.started', key: `tool-${sequence}`,
            callId: String(data.call_id || ''),
            name: String(data.name || 'tool'),
            args: (data.args || {}) as Record<string, unknown>,
          }) })))
        }
        if (type === 'tool.finished') {
          const sequence = (liveSequenceRef.current.get(sessionId) || 0) + 1
          liveSequenceRef.current.set(sessionId, sequence)
          setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, liveTimeline: reduceLiveTimeline(state.liveTimeline, {
            type: 'tool.finished', key: `tool-result-${sequence}`,
            callId: String(data.call_id || ''),
            name: String(data.name || 'tool'),
            content: String(data.content || ''),
            status: String(data.status || 'success'),
            duration: typeof data.duration_seconds === 'number' ? data.duration_seconds : null,
          }) })))
        }
        if (type === 'run.error') {
          setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, error: String(data.error) })))
        }
        if (type === 'run.timed_out') {
          setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, error: `运行超时（${String(data.timeout_seconds || '?')} 秒）` })))
        }
      }, {
        signal: abortController.signal,
        onRunId: (value) => setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, runId: value }))),
      })
    } catch (cause) {
      if (!isAbortError(cause)) {
        const message = cause instanceof Error ? cause.message : String(cause)
        setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, error: message })))
      }
    } finally {
      if (runAbortRef.current.get(sessionId) === abortController) runAbortRef.current.delete(sessionId)
      liveSequenceRef.current.delete(sessionId)
      try {
        await refreshCompletedSession(sessionId)
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : String(cause)
        setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, error: state.error || message })))
      }
      setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({
        ...idleRunState(state.error),
      })))
    }
  }

  async function stop() {
    if (!selectedId || !currentRun.runId || stopping) return
    const sessionId = selectedId
    setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, status: 'stopping' })))
    try {
      await cancelRun(currentRun.runId)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause)
      setRunStates((states) => updateSessionRunState(states, sessionId, (state) => ({ ...state, error: message })))
    } finally {
      runAbortRef.current.get(sessionId)?.abort()
    }
  }

  function beginRename(session: Session, location: 'sidebar' | 'header') {
    setEditingSessionId(session.id)
    setRenameLocation(location)
    setRenameDraft(session.title)
    setError(null)
  }

  function cancelRename() {
    setEditingSessionId(null)
    setRenameLocation(null)
    setRenameDraft('')
  }

  async function commitRename(session: Session) {
    if (renameSavingRef.current) return
    const title = normalizeSessionTitle(renameDraft)
    if (!title || title === session.title) {
      cancelRename()
      return
    }
    renameSavingRef.current = true
    setRenameSaving(true)
    try {
      const updated = await updateSession(session.id, { title })
      setSessions((items) => items.map((item) => item.id === updated.id ? updated : item))
      setDetail((current) => current?.session.id === updated.id ? { ...current, session: updated } : current)
      cancelRename()
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      renameSavingRef.current = false
      setRenameSaving(false)
    }
  }

  async function saveSessionLimits(value: SessionLimits) {
    if (limitsSavingRef.current) return
    limitsSavingRef.current = true
    setLimitsSaving(true)
    try {
      await patchCurrent(value)
      setLimitsOpen(false)
    } finally {
      limitsSavingRef.current = false
      setLimitsSaving(false)
    }
  }

  async function commitGraphEntrypoint() {
    if (!detail || graphSavingRef.current) return
    const sessionId = detail.session.id
    const entrypoint = graphDraft.trim()
    if (!entrypoint || entrypoint === detail.session.graph_entrypoint) {
      setGraphDraft(detail.session.graph_entrypoint)
      return
    }
    graphSavingRef.current = true
    setGraphSaving(true)
    try {
      const updated = await updateSession(sessionId, { graph_entrypoint: entrypoint })
      setSessions((items) => items.map((item) => item.id === sessionId ? updated : item))
      setDetail((current) => current?.session.id === sessionId ? { ...current, session: updated } : current)
      setGraphDraft(updated.graph_entrypoint)
      setError(null)
    } catch (cause) {
      setGraphDraft(detail.session.graph_entrypoint)
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      graphSavingRef.current = false
      setGraphSaving(false)
    }
  }

  async function archive(session: Session) {
    await updateSession(session.id, { archived: !showArchived })
    const remaining = sessions.filter((item) => item.id !== session.id)
    setSessions(remaining)
    if (selectedId === session.id) {
      const next = remaining[0]
      if (next) {
        await selectSession(next.id)
      } else {
        selectionSequenceRef.current += 1
        selectedIdRef.current = null
        setSelectedId(null)
        setDetail(null)
      }
    }
  }

  async function toggleArchived() {
    const nextMode = !showArchived
    const items = await getSessions(nextMode)
    showArchivedRef.current = nextMode
    setShowArchived(nextMode)
    setSessions(items)
    if (items[0]) {
      await selectSession(items[0].id)
    } else {
      selectionSequenceRef.current += 1
      selectedIdRef.current = null
      setSelectedId(null)
      setDetail(null)
    }
  }

  async function confirmDelete() {
    if (!deleteCandidate || deleting) return
    const sessionId = deleteCandidate.id
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteSession(sessionId)
      const remaining = sessions.filter((item) => item.id !== sessionId)
      setSessions(remaining)
      setRunStates((states) => {
        const next = { ...states }
        delete next[sessionId]
        return next
      })
      setDrafts((items) => {
        const next = { ...items }
        delete next[sessionId]
        return next
      })
      if (selectedIdRef.current === sessionId) {
        const next = remaining[0] || null
        if (next) {
          await selectSession(next.id)
        } else {
          selectionSequenceRef.current += 1
          selectedIdRef.current = null
          setSelectedId(null)
          setDetail(null)
        }
      }
      setDeleteCandidate(null)
      setDeleteError(null)
      setError(null)
    } catch (cause) {
      setDeleteError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="app-shell">
      {sidebarOpen && <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><Bot size={18} /></span><div><strong>LLM-Graph</strong><small>Local agent workspace</small></div><button className="icon-button" onClick={() => setSidebarOpen(false)}><PanelLeftClose size={17} /></button></div>
        <button className="new-session" onClick={() => void addSession()}><MessageSquarePlus size={17} />新建会话<span>Ctrl N</span></button>
        <div className="search-box"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索会话" /></div>
        <div className="session-list">
          {grouped.map((group) => <section key={group.label}><label>{group.label}</label>{group.items.map((session) => (
            <div key={session.id} role="button" tabIndex={0} className={`session-row ${selectedId === session.id ? 'active' : ''}`} onClick={() => void selectSession(session.id)} onKeyDown={(event) => { if (event.key === 'Enter') void selectSession(session.id) }}>
              <span className={`session-dot ${isSessionRunning(session.id) ? 'running' : ''}`} />
              <span className="session-text">
                {editingSessionId === session.id && renameLocation === 'sidebar' ? (
                  <RenameInput className="sidebar-title-input" value={renameDraft} saving={renameSaving} onChange={setRenameDraft} onCommit={() => void commitRename(session)} onCancel={cancelRename} />
                ) : <strong>{session.title}</strong>}
                <small>{formatTime(session.updated_at)} · {session.profile_name}</small>
              </span>
              <span className="session-actions">
                <button title="重命名" onClick={(event) => { event.stopPropagation(); beginRename(session, 'sidebar') }}><Pencil size={13} /></button>
                <button disabled={isSessionRunning(session.id)} title={isSessionRunning(session.id) ? '请先停止任务' : showArchived ? '恢复' : '归档'} onClick={(event) => { event.stopPropagation(); void archive(session) }}>{showArchived ? <ArchiveRestore size={14} /> : <Archive size={14} />}</button>
                <button className="delete-action" disabled={isSessionRunning(session.id)} title={isSessionRunning(session.id) ? '请先停止任务' : '永久删除'} onClick={(event) => { event.stopPropagation(); setDeleteError(null); setDeleteCandidate(session) }}><Trash2 size={13} /></button>
              </span>
            </div>
          ))}</section>)}
        </div>
        <div className="sidebar-footer"><button className={`sidebar-settings ${showArchived ? 'active' : ''}`} onClick={() => void toggleArchived()}>{showArchived ? <ArchiveRestore size={16} /> : <Archive size={16} />}{showArchived ? '返回会话' : '已归档'}</button><button className="sidebar-settings" onClick={() => setSettingsOpen(true)}><Settings size={16} />设置</button></div>
      </aside>}

      <main className="workspace">
        {!sidebarOpen && <button className="sidebar-reopen icon-button" onClick={() => setSidebarOpen(true)}><Bot size={18} /></button>}
        {detail ? <>
          <header className="workspace-header">
            <div className="title-area"><div className="title-line">
              {editingSessionId === detail.session.id && renameLocation === 'header' ? (
                <RenameInput className="header-title-input" value={renameDraft} saving={renameSaving} onChange={setRenameDraft} onCommit={() => void commitRename(detail.session)} onCancel={cancelRename} />
              ) : <h1 title="双击重命名" onDoubleClick={() => beginRename(detail.session, 'header')}>{detail.session.title}</h1>}
              <span className={`run-badge ${running ? 'active' : ''}`}>{running ? stopping ? '正在停止' : '运行中' : '就绪'}</span>
            </div><button className="path-button" disabled={running} onClick={async () => { const path = await window.llmGraph.selectDirectory(); if (path) await patchCurrent({ working_dir: path }) }}><Folder size={13} />{detail.session.working_dir}</button><label className="graph-entrypoint"><span>Graph</span><input value={graphDraft} disabled={graphLocked || graphSaving} spellCheck={false} title={graphLocked ? running ? '运行中不能更换 Graph' : '已有消息的会话不能更换 Graph' : '格式：src.graphs.<模块>:<异步函数>'} onChange={(event) => setGraphDraft(event.target.value)} onBlur={() => void commitGraphEntrypoint()} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void commitGraphEntrypoint() } if (event.key === 'Escape') setGraphDraft(detail.session.graph_entrypoint) }} /></label></div>
            <div className="header-controls"><select value={detail.session.profile_name} disabled={running} onChange={(event) => void patchCurrent({ profile_name: event.target.value })}>{profiles.map((profile) => <option key={profile.name}>{profile.name}</option>)}</select><select value={detail.session.vision_profile_name} disabled={running} onChange={(event) => void patchCurrent({ vision_profile_name: event.target.value })}>{profiles.map((profile) => <option key={profile.name}>{profile.name}</option>)}</select></div>
          </header>
          <div className="conversation" ref={conversationRef} onScroll={handleConversationScroll}>
            <div className="conversation-inner" ref={conversationInnerRef}>
              {displayMessages.length === 0 && <div className="empty-state"><span><Sparkles size={23} /></span><h2>开始一个新的工作流</h2><p>描述你希望 Agent 完成的任务。工具调用和执行结果会按步骤显示在这里。</p></div>}
              <MessageTimeline messages={displayMessages} running={running} stopping={stopping} liveTimeline={liveTimeline} />
              {currentError && <div className="error-banner"><strong>操作未完成</strong><span>{currentError}</span><button onClick={() => { setError(null); if (selectedId) setRunStates((states) => updateSessionRunState(states, selectedId, (state) => ({ ...state, error: null }))) }}><X size={15} /></button></div>}
            </div>
          </div>
          {!atBottom && <button className="to-bottom-button" type="button" aria-label="回到底部" onClick={scrollToBottom}><ArrowDown size={16} /></button>}
          <div className="composer-wrap"><div className="composer"><textarea value={draft} disabled={running} placeholder="告诉 LLM-Graph 你想完成什么…" rows={1} onChange={(event) => setCurrentDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit() } }} /><div className="composer-footer"><div className="composer-meta"><span><span className="status-dot" />{detail.session.profile_name}</span><div className="limits-control" ref={limitsMenuRef}><button className={`limits-trigger ${limitsOpen ? 'active' : ''}`} type="button" disabled={running} aria-expanded={limitsOpen} onClick={() => setLimitsOpen((value) => !value)}><Gauge size={12} />{formatContextLimit(detail.session.context_window_tokens)} ctx<ChevronDown size={11} /></button>{limitsOpen && <SessionLimitsMenu key={`${detail.session.id}-${detail.session.context_window_tokens}-${detail.session.recursion_limit}`} session={detail.session} saving={limitsSaving} onSave={saveSessionLimits} />}</div></div>{running ? <button className="stop-button" disabled={!currentRun.runId || stopping} onClick={() => void stop()}><CircleStop size={16} />{stopping ? '正在停止' : '停止'}</button> : <button className="send-button" disabled={!draft.trim() || limitsSaving} onClick={() => void submit()}><Send size={16} />发送</button>}</div></div><p className="composer-hint">Enter 发送 · Shift Enter 换行 · 工具会在当前工作目录中运行</p></div>
        </> : <div className="empty-workspace"><Bot size={28} /><p>创建一个会话开始使用</p><button className="primary-button" onClick={() => void addSession()}>新建会话</button></div>}
      </main>
      {settingsOpen && <SettingsDialog profiles={profiles} initial={defaults} onClose={() => setSettingsOpen(false)} onSave={async (value) => { const graphEntrypoint = await validateGraphEntrypoint(value.graph_entrypoint); const normalized = { ...value, graph_entrypoint: graphEntrypoint }; localStorage.setItem(DEFAULTS_KEY, JSON.stringify(normalized)); setSavedDefaults(normalized); setThemePreference(value.theme); setSettingsOpen(false) }} />}
      {deleteCandidate && <DeleteDialog session={deleteCandidate} deleting={deleting} error={deleteError} onClose={() => { if (!deleting) { setDeleteCandidate(null); setDeleteError(null) } }} onConfirm={confirmDelete} />}
    </div>
  )
}
