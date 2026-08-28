import type { LiveTimelinePart, Message, ToolCall } from './types'

export interface TimelineToolPart {
  type: 'tool'
  key: string
  callId: string
  name: string
  args?: Record<string, unknown>
  content?: string
  status?: string | null
}

export interface TimelineTextPart {
  type: 'execution' | 'assistant'
  key: string
  content: string
}

export type TimelinePart = TimelineToolPart | TimelineTextPart

export type LiveTimelineAction =
  | { type: 'assistant.step'; key: string; content: string }
  | { type: 'tool.started'; key: string; callId: string; name: string; args: Record<string, unknown> }
  | { type: 'tool.finished'; key: string; callId: string; name: string; content: string; status: string; duration?: number | null }

export interface ConversationTurn {
  key: string
  user?: Message
  parts: TimelinePart[]
}

function messageKey(message: Message, index: number) {
  return message.id || `${message.role}-${index}`
}

function toolPart(call: ToolCall, message: Message, index: number, callIndex: number): TimelineToolPart {
  const callId = call.id || `${messageKey(message, index)}-call-${callIndex}`
  return {
    type: 'tool',
    key: `tool-${callId}`,
    callId,
    name: call.name || 'tool',
    args: call.args,
    status: 'pending',
  }
}

/**
 * Projects LangChain's flat checkpoint messages into stable user turns.
 * Assistant messages that request tools are execution notes; the last plain
 * assistant message remains the answer. Tool results are merged into the
 * placeholder created by their matching tool call.
 */
export function buildConversationTurns(messages: Message[]): ConversationTurn[] {
  const turns: ConversationTurn[] = []
  const tools = new Map<string, TimelineToolPart>()
  let current: ConversationTurn | undefined

  const ensureTurn = (key: string) => {
    if (!current) {
      current = { key, parts: [] }
      turns.push(current)
    }
    return current
  }

  messages.forEach((message, index) => {
    if (message.role === 'system') return
    const key = messageKey(message, index)

    if (message.role === 'user') {
      current = { key: `turn-${key}`, user: message, parts: [] }
      turns.push(current)
      return
    }

    const turn = ensureTurn(`turn-${key}`)
    if (message.role === 'assistant') {
      const calls = message.tool_calls || []
      if (calls.length > 0) {
        if (message.content.trim()) {
          turn.parts.push({ type: 'execution', key: `execution-${key}`, content: message.content })
        }
        calls.forEach((call, callIndex) => {
          const part = toolPart(call, message, index, callIndex)
          turn.parts.push(part)
          if (call.id) tools.set(call.id, part)
        })
      } else if (message.content.trim()) {
        turn.parts.push({ type: 'assistant', key: `assistant-${key}`, content: message.content })
      }
      return
    }

    if (message.role === 'tool') {
      const callId = message.tool_call_id || ''
      const existing = tools.get(callId)
      if (existing) {
        existing.content = message.content
        existing.status = message.status || 'success'
        if (message.name) existing.name = message.name
        return
      }
      turn.parts.push({
        type: 'tool',
        key: `tool-result-${key}`,
        callId: callId || key,
        name: message.name || 'tool',
        content: message.content,
        status: message.status || 'success',
      })
    }
  })

  return turns.filter((turn) => turn.user || turn.parts.length > 0)
}

export function summarizeToolArgs(args?: Record<string, unknown>, maxLength = 120) {
  if (!args || Object.keys(args).length === 0) return '已执行工具'
  const text = JSON.stringify(args)
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text
}

/**
 * Applies one SSE execution event to the live timeline. Tool completion updates
 * the matching call in place so the UI keeps the order in which work started.
 */
export function reduceLiveTimeline(
  parts: LiveTimelinePart[],
  action: LiveTimelineAction,
): LiveTimelinePart[] {
  if (action.type === 'assistant.step') {
    if (!action.content.trim()) return parts
    return [...parts, { type: 'step', key: action.key, content: action.content }]
  }

  if (action.type === 'tool.started') {
    const existingIndex = action.callId
      ? parts.findIndex((part) => part.type === 'tool' && part.callId === action.callId)
      : -1
    const nextTool: LiveTimelinePart = {
      type: 'tool',
      key: action.key,
      callId: action.callId,
      name: action.name,
      args: action.args,
      status: 'running',
    }
    if (existingIndex < 0) return [...parts, nextTool]
    return parts.map((part, index) => index === existingIndex
      ? { ...nextTool, key: part.key }
      : part)
  }

  const existingIndex = action.callId
    ? parts.findIndex((part) => part.type === 'tool' && part.callId === action.callId)
    : -1
  if (existingIndex < 0) {
    return [...parts, {
      type: 'tool',
      key: action.key,
      callId: action.callId,
      name: action.name,
      args: {},
      content: action.content,
      status: action.status,
      duration: action.duration,
    }]
  }
  return parts.map((part, index) => index === existingIndex && part.type === 'tool'
    ? {
        ...part,
        name: action.name || part.name,
        content: action.content,
        status: action.status,
        duration: action.duration,
      }
    : part)
}
