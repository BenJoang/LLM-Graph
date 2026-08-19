export {}

declare global {
  interface Window {
    llmGraph: {
      getBackendConnection(): Promise<{ baseUrl: string; token: string }>
      selectDirectory(): Promise<string | null>
      setNativeTheme(theme: 'light' | 'dark' | 'system'): Promise<void>
    }
  }
}
