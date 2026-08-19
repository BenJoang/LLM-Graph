export const CONTEXT_PRESETS = [32768, 65536, 131072, 262144] as const
export const MIN_CONTEXT_TOKENS = 1024
export const MAX_CONTEXT_TOKENS = 2_000_000
export const MIN_RECURSION_LIMIT = 1
export const MAX_RECURSION_LIMIT = 1000

export interface SessionLimits {
  context_window_tokens: number
  recursion_limit: number
}

export interface SessionLimitsDraft {
  contextWindow: string
  recursionLimit: string
}

export type SessionLimitsValidation =
  | { ok: true; value: SessionLimits }
  | { ok: false; error: string }

function integer(value: string) {
  const trimmed = value.trim()
  if (!/^\d+$/.test(trimmed)) return null
  const parsed = Number(trimmed)
  return Number.isSafeInteger(parsed) ? parsed : null
}

export function validateSessionLimits(draft: SessionLimitsDraft): SessionLimitsValidation {
  const contextWindow = integer(draft.contextWindow)
  if (contextWindow === null || contextWindow < MIN_CONTEXT_TOKENS || contextWindow > MAX_CONTEXT_TOKENS) {
    return { ok: false, error: '上下文窗口必须是 1,024–2,000,000 之间的整数' }
  }
  const recursionLimit = integer(draft.recursionLimit)
  if (recursionLimit === null || recursionLimit < MIN_RECURSION_LIMIT || recursionLimit > MAX_RECURSION_LIMIT) {
    return { ok: false, error: '递归上限必须是 1–1,000 之间的整数' }
  }
  return {
    ok: true,
    value: {
      context_window_tokens: contextWindow,
      recursion_limit: recursionLimit,
    },
  }
}

export function normalizeSessionTitle(value: string) {
  return value.trim().replace(/\s+/g, ' ').slice(0, 120)
}

export function formatContextLimit(value: number) {
  if (value >= 1000 && value % 1024 === 0) return `${value / 1024}K`
  return value.toLocaleString('zh-CN')
}
