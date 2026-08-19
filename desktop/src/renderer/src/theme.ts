export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export function normalizeThemePreference(value: unknown): ThemePreference {
  return value === 'dark' || value === 'system' ? value : 'light'
}

export function resolveTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean,
): ResolvedTheme {
  if (preference === 'system') return systemPrefersDark ? 'dark' : 'light'
  return preference
}

export function applyDocumentTheme(theme: ResolvedTheme) {
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
}
