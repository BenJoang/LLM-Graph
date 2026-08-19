import type { StreamEvent } from './types'

export function consumeSseBuffer(buffer: string): {
  events: StreamEvent[]
  remainder: string
} {
  const normalized = buffer.replaceAll('\r\n', '\n')
  const blocks = normalized.split('\n\n')
  const remainder = blocks.pop() || ''
  const events = blocks.flatMap((block) => {
    let type = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) type = line.slice(6).trim()
      if (line.startsWith('data:')) data.push(line.slice(5).trim())
    }
    if (!data.length) return []
    return [{ type, data: JSON.parse(data.join('\n')) as Record<string, unknown> }]
  })
  return { events, remainder }
}
