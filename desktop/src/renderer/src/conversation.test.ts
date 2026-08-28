import { describe, expect, it } from 'vitest'
import {
  buildConversationTurns,
  reduceLiveTimeline,
  summarizeToolArgs,
  type LiveTimelineAction,
} from './conversation'
import type { LiveTimelinePart, Message } from './types'

const message = (input: Partial<Message> & Pick<Message, 'role'>): Message => ({
  id: input.id || '',
  content: input.content || '',
  tool_calls: input.tool_calls || [],
  ...input,
})

describe('conversation turn projection', () => {
  it('groups tool execution and the final answer under its user turn', () => {
    const turns = buildConversationTurns([
      message({ id: 'u1', role: 'user', content: '查找配置' }),
      message({
        id: 'a1',
        role: 'assistant',
        content: '我先搜索项目。',
        tool_calls: [{ id: 'c1', name: 'grep', args: { pattern: 'config' } }],
      }),
      message({ id: 't1', role: 'tool', content: '2 matches', tool_call_id: 'c1', name: 'grep' }),
      message({ id: 'a2', role: 'assistant', content: '已经找到两个配置文件。' }),
    ])

    expect(turns).toHaveLength(1)
    expect(turns[0].user?.content).toBe('查找配置')
    expect(turns[0].parts.map((part) => part.type)).toEqual(['execution', 'tool', 'assistant'])
    expect(turns[0].parts[1]).toMatchObject({ name: 'grep', content: '2 matches', status: 'success' })
  })

  it('keeps parallel calls ordered and unmatched tool results visible', () => {
    const turns = buildConversationTurns([
      message({ id: 'u1', role: 'user', content: '检查' }),
      message({
        id: 'a1', role: 'assistant', tool_calls: [
          { id: 'c1', name: 'read', args: { path: 'a.ts' } },
          { id: 'c2', name: 'read', args: { path: 'b.ts' } },
        ],
      }),
      message({ id: 't2', role: 'tool', content: 'B', tool_call_id: 'c2', status: 'error' }),
      message({ id: 'orphan', role: 'tool', content: 'extra', tool_call_id: 'missing', name: 'other' }),
    ])

    expect(turns[0].parts).toHaveLength(3)
    expect(turns[0].parts[0]).toMatchObject({ type: 'tool', name: 'read', status: 'pending' })
    expect(turns[0].parts[1]).toMatchObject({ type: 'tool', content: 'B', status: 'error' })
    expect(turns[0].parts[2]).toMatchObject({ type: 'tool', name: 'other', content: 'extra' })
  })

  it('truncates long argument summaries without changing details', () => {
    const summary = summarizeToolArgs({ query: 'x'.repeat(200) }, 40)
    expect(summary).toHaveLength(40)
    expect(summary.endsWith('…')).toBe(true)
  })
})

function applyLiveActions(actions: LiveTimelineAction[]) {
  return actions.reduce<LiveTimelinePart[]>(reduceLiveTimeline, [])
}

describe('live timeline projection', () => {
  it('interleaves consecutive assistant steps and tools in event order', () => {
    const parts = applyLiveActions([
      { type: 'assistant.step', key: 's1', content: '思考 A' },
      { type: 'tool.started', key: 't1', callId: 'c1', name: 'grep', args: { pattern: 'a' } },
      { type: 'tool.finished', key: 'r1', callId: 'c1', name: 'grep', content: 'A', status: 'success', duration: 0.2 },
      { type: 'assistant.step', key: 's2', content: '思考 B' },
      { type: 'tool.started', key: 't2', callId: 'c2', name: 'read', args: { path: 'b.ts' } },
    ])

    expect(parts.map((part) => part.type)).toEqual(['step', 'tool', 'step', 'tool'])
    expect(parts[1]).toMatchObject({ key: 't1', callId: 'c1', content: 'A', status: 'success' })
    expect(parts[2]).toMatchObject({ key: 's2', content: '思考 B' })
  })

  it('keeps parallel tools in start order when they finish out of order', () => {
    const parts = applyLiveActions([
      { type: 'tool.started', key: 't1', callId: 'c1', name: 'read', args: { path: 'a.ts' } },
      { type: 'tool.started', key: 't2', callId: 'c2', name: 'read', args: { path: 'b.ts' } },
      { type: 'tool.finished', key: 'r2', callId: 'c2', name: 'read', content: 'B', status: 'success' },
      { type: 'tool.finished', key: 'r1', callId: 'c1', name: 'read', content: 'A', status: 'error' },
    ])

    expect(parts.map((part) => part.type === 'tool' ? part.callId : '')).toEqual(['c1', 'c2'])
    expect(parts[0]).toMatchObject({ key: 't1', content: 'A', status: 'error' })
    expect(parts[1]).toMatchObject({ key: 't2', content: 'B', status: 'success' })
  })

  it('ignores empty steps, updates duplicate starts, and preserves orphan results', () => {
    const parts = applyLiveActions([
      { type: 'assistant.step', key: 'empty', content: '   ' },
      { type: 'tool.started', key: 't1', callId: 'c1', name: 'read', args: { path: 'old.ts' } },
      { type: 'tool.started', key: 'duplicate', callId: 'c1', name: 'read_file', args: { path: 'new.ts' } },
      { type: 'tool.finished', key: 'orphan', callId: 'missing', name: 'grep', content: 'result', status: 'success' },
    ])

    expect(parts).toHaveLength(2)
    expect(parts[0]).toMatchObject({ key: 't1', callId: 'c1', name: 'read_file', args: { path: 'new.ts' } })
    expect(parts[1]).toMatchObject({ key: 'orphan', callId: 'missing', name: 'grep', content: 'result' })
  })

  it('retains every step in a long-running task', () => {
    const actions: LiveTimelineAction[] = Array.from({ length: 20 }, (_, index) => ({
      type: 'assistant.step',
      key: `s${index}`,
      content: `step ${index}`,
    }))

    const parts = applyLiveActions(actions)

    expect(parts).toHaveLength(20)
    expect(parts[0]).toMatchObject({ content: 'step 0' })
    expect(parts[19]).toMatchObject({ content: 'step 19' })
  })
})
