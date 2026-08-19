import { describe, expect, it } from 'vitest'
import { isNearBottom } from './scroll'

describe('isNearBottom', () => {
  it('keeps follow ownership inside the 72px threshold', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 628, clientHeight: 300 })).toBe(true)
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 627, clientHeight: 300 })).toBe(false)
  })

  it('treats short content and the exact floor as pinned', () => {
    expect(isNearBottom({ scrollHeight: 200, scrollTop: 0, clientHeight: 300 })).toBe(true)
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 700, clientHeight: 300 })).toBe(true)
  })
})
