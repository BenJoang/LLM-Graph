import type { Profile, Session, SessionDetail, StreamEvent } from './types'
import { consumeSseBuffer } from './sse'

let connectionPromise: ReturnType<typeof window.llmGraph.getBackendConnection> | null = null

function connection() {
  connectionPromise ??= window.llmGraph.getBackendConnection()
  return connectionPromise
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const backend = await connection()
  const headers = new Headers(init?.headers)
  headers.set('Authorization', `Bearer ${backend.token}`)
  if (init?.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${backend.baseUrl}${path}`, { ...init, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail || `请求失败：${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function getProfiles(): Promise<Profile[]> {
  return (await request<{ profiles: Profile[] }>('/api/profiles')).profiles
}

export async function getSessions(archived = false): Promise<Session[]> {
  return (await request<{ sessions: Session[] }>(`/api/sessions?archived=${archived}`)).sessions
}

export async function createSession(input: Partial<Session>): Promise<Session> {
  return (
    await request<{ session: Session }>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify(input),
    })
  ).session
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${sessionId}`)
}

export async function updateSession(
  sessionId: string,
  patch: Partial<Session>,
): Promise<Session> {
  return (
    await request<{ session: Session }>(`/api/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  ).session
}

export async function cancelRun(runId: string) {
  return request(`/api/runs/${runId}/cancel`, { method: 'POST' })
}

export async function streamRun(
  sessionId: string,
  question: string,
  onEvent: (event: StreamEvent) => void,
  options: {
    signal?: AbortSignal
    onRunId?: (runId: string) => void
  } = {},
) {
  const backend = await connection()
  const response = await fetch(`${backend.baseUrl}/api/sessions/${sessionId}/runs`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${backend.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ question }),
    signal: options.signal,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail || `运行失败：${response.status}`)
  }
  if (!response.body) throw new Error('后端没有返回事件流')

  const headerRunId = response.headers.get('X-Run-ID')
  if (headerRunId) options.onRunId?.(headerRunId)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const parsed = consumeSseBuffer(buffer)
    buffer = parsed.remainder
    parsed.events.forEach(onEvent)
    if (done) break
  }
}
