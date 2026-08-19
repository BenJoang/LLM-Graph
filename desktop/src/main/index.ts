import { spawn, spawnSync, type ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { createServer } from 'node:net'
import { join, resolve } from 'node:path'
import { app, BrowserWindow, dialog, ipcMain, nativeTheme } from 'electron'

let mainWindow: BrowserWindow | null = null
let backendProcess: ChildProcess | null = null
let backendConnection: { baseUrl: string; token: string } | null = null
let quitting = false

nativeTheme.themeSource = 'light'

function projectRoot() {
  return process.env.LLM_GRAPH_PROJECT_ROOT || resolve(__dirname, '../../..')
}

async function freePort(): Promise<number> {
  return await new Promise((resolvePort, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        reject(new Error('无法分配本地端口'))
        return
      }
      server.close(() => resolvePort(address.port))
    })
  })
}

async function startBackend() {
  const port = await freePort()
  const token = randomBytes(32).toString('hex')
  const directPython = process.env.LLM_GRAPH_PYTHON
  const command = directPython || (process.platform === 'win32' ? 'conda.exe' : 'conda')
  const args = directPython
    ? ['-m', 'src.api.run_chat_api']
    : ['run', '--no-capture-output', '-n', 'LLMv1', 'python', '-m', 'src.api.run_chat_api']

  backendProcess = spawn(command, args, {
    cwd: projectRoot(),
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONUTF8: '1',
      LLM_GRAPH_GUI_PORT: String(port),
      LLM_GRAPH_GUI_TOKEN: token,
    },
  })
  backendProcess.stdout?.on('data', (chunk) => process.stdout.write(`[backend] ${chunk}`))
  backendProcess.stderr?.on('data', (chunk) => process.stderr.write(`[backend] ${chunk}`))

  const baseUrl = `http://127.0.0.1:${port}`
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    if (backendProcess.exitCode !== null) {
      throw new Error(`Python 后端已退出，代码 ${backendProcess.exitCode}`)
    }
    try {
      const response = await fetch(`${baseUrl}/health`)
      if (response.ok) {
        backendConnection = { baseUrl, token }
        return
      }
    } catch {
      // 后端仍在启动。
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 250))
  }
  throw new Error('等待 Python 后端启动超时')
}

function stopBackend() {
  if (!backendProcess?.pid || backendProcess.exitCode !== null) return
  if (process.platform === 'win32') {
    spawnSync('taskkill.exe', ['/pid', String(backendProcess.pid), '/T', '/F'], {
      windowsHide: true,
    })
    return
  }
  backendProcess.kill('SIGTERM')
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 980,
    minHeight: 660,
    show: false,
    backgroundColor: '#f9fafb',
    title: 'LLM-Graph',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })
  mainWindow.once('ready-to-show', () => mainWindow?.show())
  mainWindow.webContents.on('preload-error', (_event, path, error) => {
    console.error(`preload 加载失败：${path}`, error)
  })
  mainWindow.webContents.on('did-fail-load', (_event, code, description) => {
    console.error(`renderer 加载失败：${code} ${description}`)
  })
  if (process.env.ELECTRON_RENDERER_URL) {
    void mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

ipcMain.handle('backend:connection', () => {
  if (!backendConnection) throw new Error('后端尚未就绪')
  return backendConnection
})

ipcMain.handle('dialog:select-directory', async () => {
  if (!mainWindow) return null
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: '选择 Agent 工作目录',
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('theme:set', (_event, source: unknown) => {
  if (source !== 'light' && source !== 'dark' && source !== 'system') {
    throw new Error('无效的主题设置')
  }
  nativeTheme.themeSource = source
})

app.whenReady().then(async () => {
  try {
    await startBackend()
    createWindow()
  } catch (error) {
    dialog.showErrorBox('LLM-Graph 启动失败', error instanceof Error ? error.message : String(error))
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && backendConnection) createWindow()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (quitting) return
  quitting = true
  stopBackend()
})

process.once('SIGINT', () => {
  stopBackend()
  app.quit()
})

process.once('SIGTERM', () => {
  stopBackend()
  app.quit()
})

process.once('exit', stopBackend)
