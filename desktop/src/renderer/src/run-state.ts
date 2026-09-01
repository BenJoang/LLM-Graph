import type { LiveTimelinePart, Message } from './types'

export type RunStatus = 'idle' | 'running' | 'stopping'

export interface SessionRunState {
  status: RunStatus
  runId: string | null
  liveTimeline: LiveTimelinePart[]
  optimisticMessage: Message | null
  error: string | null
}

export type SessionRunStates = Record<string, SessionRunState>

export function idleRunState(error: string | null = null): SessionRunState {
  return {
    status: 'idle',
    runId: null,
    liveTimeline: [],
    optimisticMessage: null,
    error,
  }
}

export function updateSessionRunState(
  states: SessionRunStates,
  sessionId: string,
  update: (state: SessionRunState) => SessionRunState,
): SessionRunStates {
  return {
    ...states,
    [sessionId]: update(states[sessionId] || idleRunState()),
  }
}

export function messagesWithOptimistic(
  messages: Message[],
  optimistic: Message | null | undefined,
): Message[] {
  if (!optimistic) return messages
  const latestMatchingUser = [...messages]
    .reverse()
    .find((message) => message.role === 'user')
  if (latestMatchingUser?.content === optimistic.content) return messages
  return [...messages, optimistic]
}
