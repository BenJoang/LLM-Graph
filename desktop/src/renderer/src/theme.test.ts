import { describe, expect, it } from 'vitest'
import { normalizeThemePreference, resolveTheme } from './theme'

describe('theme preference', () => {
  it('defaults unknown and missing values to light', () => {
    expect(normalizeThemePreference(undefined)).toBe('light')
    expect(normalizeThemePreference('sepia')).toBe('light')
  })

  it('keeps explicit preferences and resolves system', () => {
    expect(normalizeThemePreference('dark')).toBe('dark')
    expect(normalizeThemePreference('system')).toBe('system')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('system', true)).toBe('dark')
  })
})
