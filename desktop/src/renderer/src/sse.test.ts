import { describe, expect, it } from 'vitest'
import { consumeSseBuffer } from './sse'

describe('consumeSseBuffer', () => {
  it('parses named events and keeps an incomplete trailing event', () => {
    const result = consumeSseBuffer(
      'event: run.started\r\ndata: {"run_id":"run-1"}\r\n\r\n' +
      'event: tool.started\ndata: {"name":"grep"}',
    )
    expect(result.events).toEqual([
      { type: 'run.started', data: { run_id: 'run-1' } },
    ])
    expect(result.remainder).toContain('tool.started')
  })

  it('parses multiple complete events', () => {
    const result = consumeSseBuffer(
      'event: tool.finished\ndata: {"status":"success"}\n\n' +
      'event: run.completed\ndata: {"session_id":"gui-1"}\n\n',
    )
    expect(result.events.map((event) => event.type)).toEqual([
      'tool.finished',
      'run.completed',
    ])
    expect(result.remainder).toBe('')
  })
})
