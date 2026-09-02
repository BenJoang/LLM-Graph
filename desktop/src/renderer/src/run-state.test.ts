import { describe, expect, it } from 'vitest'

import {
  idleRunState,
  messagesWithOptimistic,
  updateSessionRunState,
} from './run-state'

describe('per-session run state', () => {
  it('updates one session without changing another', () => {
    const initial = {
      first: { ...idleRunState(), status: 'running' as const, runId: 'run-1' },
      second: idleRunState(),
    }
    const updated = updateSessionRunState(initial, 'second', (state) => ({
      ...state,
      error: 'second failed',
    }))

    expect(updated.first).toBe(initial.first)
    expect(updated.second.error).toBe('second failed')
  })

  it('does not duplicate an optimistic user message already checkpointed', () => {
    const optimistic = {
      id: 'optimistic',
      role: 'user',
      content: '同一个问题',
      tool_calls: [],
    }
    const checkpointed = [{ ...optimistic, id: 'persisted' }]

    expect(messagesWithOptimistic([], optimistic)).toEqual([optimistic])
    expect(messagesWithOptimistic(checkpointed, optimistic)).toBe(checkpointed)
  })

  it('preserves the message array reference when there is no optimistic message', () => {
    const messages = [{
      id: 'persisted',
      role: 'assistant',
      content: '完成',
      tool_calls: [],
    }]

    expect(messagesWithOptimistic(messages, null)).toBe(messages)
  })
})
