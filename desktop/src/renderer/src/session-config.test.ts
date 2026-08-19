import { describe, expect, it } from 'vitest'
import { formatContextLimit, normalizeSessionTitle, validateSessionLimits } from './session-config'

describe('session config helpers', () => {
  it('validates context and recursion boundaries', () => {
    expect(validateSessionLimits({ contextWindow: '1024', recursionLimit: '1' }).ok).toBe(true)
    expect(validateSessionLimits({ contextWindow: '2000000', recursionLimit: '1000' }).ok).toBe(true)
    expect(validateSessionLimits({ contextWindow: '1023', recursionLimit: '20' }).ok).toBe(false)
    expect(validateSessionLimits({ contextWindow: '32768', recursionLimit: '1001' }).ok).toBe(false)
    expect(validateSessionLimits({ contextWindow: '32.5', recursionLimit: '20' }).ok).toBe(false)
  })

  it('normalizes titles and compact context labels', () => {
    expect(normalizeSessionTitle('  新的   会话\n标题  ')).toBe('新的 会话 标题')
    expect(normalizeSessionTitle('x'.repeat(140))).toHaveLength(120)
    expect(formatContextLimit(32768)).toBe('32K')
    expect(formatContextLimit(33000)).toBe('33,000')
  })
})
