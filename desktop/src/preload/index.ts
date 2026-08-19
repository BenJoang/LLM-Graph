import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('llmGraph', {
  getBackendConnection: () => ipcRenderer.invoke('backend:connection'),
  selectDirectory: () => ipcRenderer.invoke('dialog:select-directory'),
  setNativeTheme: (theme: 'light' | 'dark' | 'system') =>
    ipcRenderer.invoke('theme:set', theme),
})
