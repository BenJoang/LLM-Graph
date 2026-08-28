import { beforeAll, describe, expect, it, vi } from 'vitest'

import { streamRun } from './api'

beforeAll(() => {
  vi.stubGlobal('window', {
    llmGraph: {
      getBackendConnection: async () => ({
        baseUrl: 'http://127.0.0.1:8100',
        token: 'test-token',
      }),
    },
  })
})

describe('streamRun', () => {
  it('exposes the run id immediately and forwards the abort signal', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      expect(init?.signal).toBe(controller.signal)
      return new Response(
        'event: run.started\ndata: {"run_id":"run-body"}\n\n',
        {
          status: 200,
          headers: {
            'Content-Type': 'text/event-stream',
            'X-Run-ID': 'run-header',
          },
        },
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const runIds: string[] = []
    const events: string[] = []
    await streamRun(
      'session-1',
      'hello',
      (event) => events.push(event.type),
      {
        signal: controller.signal,
        onRunId: (runId) => runIds.push(runId),
      },
    )

    expect(runIds).toEqual(['run-header'])
    expect(events).toEqual(['run.started'])
    expect(fetchMock).toHaveBeenCalledOnce()
  })
})
