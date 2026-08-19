import { describe, expect, it } from 'vitest'
import { buildConversationTurns, summarizeToolArgs } from './conversation'
import type { Message } from './types'

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
