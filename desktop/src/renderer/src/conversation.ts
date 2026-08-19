import type { Message, ToolCall } from './types'

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
