import { app, BrowserWindow, ipcMain, dialog, nativeTheme } from 'electron'
import path from 'path'
import { existsSync } from 'fs'
import { registerDeployment, deploymentBusy } from './deployment'
import { spawn } from 'child_process'
import fs from 'fs/promises'
import type { EgressEntry, EngineState, SaveResult } from '../types/electron'

let mainWindow: BrowserWindow | null = null

const FIREKB_ROOT = process.env.FIREKB_ROOT || path.join(__dirname, '..', '..', '..')
const PYTHON_PATH = process.env.PYTHON_PATH || (existsSync(path.join(FIREKB_ROOT, '.venv/Scripts/python.exe')) ? path.join(FIREKB_ROOT, '.venv/Scripts/python.exe') : 'python')
registerDeployment(FIREKB_ROOT, PYTHON_PATH, () => mainWindow)

// 桌面端只编辑 FireKB 既有的两份配置，不另存一份副本
const CONFIG_DIR = path.join(FIREKB_ROOT, '00_配置')
const ENGINES_PATH = path.join(CONFIG_DIR, 'engines.json')
const ENV_PATH = path.join(CONFIG_DIR, '.env')

// 与 engines.json 的三个键一一对应（EngineKind 的类型在 src/types/electron.d.ts 中声明）
const ENGINE_KINDS = ['parser', 'ocr', 'asr'] as const

// .env 中允许在界面上编辑的键（其余键一律原样保留）
const ENV_EDITABLE_KEYS = [
  'FIREKB_PROVIDER',
  'DEEPSEEK_API_KEY',
  'DEEPSEEK_BASE_URL',
  'DEEPSEEK_MODEL',
  'DEEPSEEK_MODEL_STRONG'
]

let appearanceSave = Promise.resolve()

function windowColors() {
  return nativeTheme.shouldUseDarkColors
    ? { color: '#14171c', symbolColor: '#dfe5ed' }
    : { color: '#ffffff', symbolColor: '#28313d' }
}
function syncWindowAppearance() {
  const colors = windowColors()
  mainWindow?.setBackgroundColor(colors.color)
  if (process.platform === 'win32') mainWindow?.setTitleBarOverlay({ ...colors, height: 38 })
}
nativeTheme.on('updated', syncWindowAppearance)

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: 'hiddenInset',
    backgroundColor: windowColors().color,
    ...(process.platform === 'win32' ? {
      titleBarOverlay: { ...windowColors(), height: 38 }
    } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  })

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
  } else {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(async () => {
  nativeTheme.themeSource = 'light'
  try {
    const saved = JSON.parse(await fs.readFile(path.join(app.getPath('userData'), 'appearance.json'), 'utf-8'))
    if (['light', 'dark', 'system'].includes(saved.mode)) nativeTheme.themeSource = saved.mode
  } catch { /* First launch uses the reference's light appearance. */ }
  createWindow()
})

ipcMain.handle('appearance:setMode', async (_event, mode: unknown) => {
  if (mode !== 'light' && mode !== 'dark' && mode !== 'system') throw new Error('Invalid appearance mode')
  if (nativeTheme.themeSource === mode) return
  nativeTheme.themeSource = mode
  syncWindowAppearance()
  // Serialize quick switches so the last preference is also the last disk write.
  appearanceSave = appearanceSave.catch(() => undefined).then(() =>
    writeFileAtomic(path.join(app.getPath('userData'), 'appearance.json'), JSON.stringify({ mode }))
  )
  await appearanceSave
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})

// ---------------------------------------------------------------------------
// 通用辅助：子进程 / 文件读写
// ---------------------------------------------------------------------------

interface PythonResult {
  code: number
  stdout: string
  stderr: string
}

/** 运行一条 Python 命令（不转发到渲染进程，用于内部探测） */
function runPython(args: string[], timeoutMs = 60000): Promise<PythonResult> {
  return new Promise((resolve) => {
    const child = spawn(PYTHON_PATH, args, {
      cwd: FIREKB_ROOT,
      windowsHide: true,
      env: { ...process.env, FIREKB_ROOT, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
    })

    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => child.kill(), timeoutMs)

    child.stdout.on('data', (data: Buffer) => {
      stdout += data.toString()
    })
    child.stderr.on('data', (data: Buffer) => {
      stderr += data.toString()
    })
    child.on('error', (err: Error) => {
      clearTimeout(timer)
      resolve({ code: -1, stdout, stderr: `${stderr}${err.message}` })
    })
    child.on('close', (code: number | null) => {
      clearTimeout(timer)
      resolve({ code: code === null ? -1 : code, stdout, stderr })
    })
  })
}

/**
 * 原子写：先写同目录下的临时文件再改名。
 * 直接覆盖写配置有个真实风险 —— 写到一半失败会留下半截文件，
 * 而 engines.json / .env 一旦损坏，整条流水线都起不来。
 */
async function writeFileAtomic(target: string, content: string) {
  const tmp = `${target}.tmp-${Date.now()}`
  await fs.writeFile(tmp, content, 'utf-8')
  await fs.rename(tmp, target)
}

/** 解析 .env：只取键值，注释与空行由 updateEnvText 负责保留 */
function parseEnvText(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const idx = trimmed.indexOf('=')
    if (idx <= 0) continue
    out[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim()
  }
  return out
}

/**
 * 更新 .env 文本：只替换已知键，**保留原有注释、空行与顺序**，新键追加到末尾。
 * 不用「解析成对象再序列化」的做法 —— 那会把 .env 里给使用者看的说明注释全部抹掉。
 */
function updateEnvText(text: string, changes: Record<string, string>): string {
  const pending = { ...changes }
  const lines = text.split('\n').map((line) => {
    const idx = line.indexOf('=')
    if (idx <= 0 || line.trim().startsWith('#')) return line
    const key = line.slice(0, idx).trim()
    if (!(key in pending)) return line
    const value = pending[key]
    delete pending[key]
    return `${key}=${value}`
  })

  const rest = Object.keys(pending)
  if (rest.length) {
    if (lines.length && lines[lines.length - 1].trim() !== '') lines.push('')
    for (const key of rest) lines.push(`${key}=${pending[key]}`)
  }
  return lines.join('\n')
}

/** 密钥掩码：界面上只给前后各几位，原文不出主进程 */
function maskSecret(value: string): string {
  if (!value) return ''
  if (value.length <= 12) return '********'
  return `${value.slice(0, 6)}...${value.slice(-4)}`
}

/**
 * 就地改写 JSON 文本里的顶层键值 —— **只动目标那几行，其余逐字节保留**。
 *
 * 为什么不 `JSON.parse` → `JSON.stringify`：那会把整份文件重排。实测后果是
 * `_可用引擎` 里的紧凑数组 `["a", "b"]` 被拆成三行（55 行变 62 行），
 * 而本项目的两台机器是用 MD5 **逐文件比对**来维持「逐字节同源」的 ——
 * 格式一变就会报"两机不一致"，使用者还得为此白同步一轮。
 * 配置是人写给人看的（里面大量说明性注释键），编辑器不该顺手改它的排版。
 *
 * → 返回改写后的文本，以及原文件里没有、需要追加的键名。
 */
function patchJsonTopLevel(
  text: string,
  changes: Record<string, string>
): { text: string; missing: string[] } {
  const pending = { ...changes }
  const lines = text.split('\n')

  for (let i = 0; i < lines.length; i++) {
    // 只认顶层键：恰好两格缩进，值为一行内的标量
    const m = lines[i].match(/^(\s{2})"([^"]+)"\s*:\s*(?:"[^"]*"|[^,{}\[\]]+?)(,?)\s*$/)
    if (!m) continue
    const [, indent, key, comma] = m
    if (!(key in pending)) continue
    lines[i] = `${indent}"${key}": ${JSON.stringify(pending[key])}${comma}`
    delete pending[key]
  }

  const missing = Object.keys(pending)
  if (missing.length) {
    // 追加到最外层闭合括号之前（找最后一个顶格的 `}`）
    let closeIdx = -1
    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].trim() === '}') {
        closeIdx = i
        break
      }
    }
    if (closeIdx > 0) {
      const prev = closeIdx - 1
      if (prev >= 0 && lines[prev].trim() && !lines[prev].trimEnd().endsWith(',')) {
        lines[prev] = `${lines[prev].trimEnd()},`
      }
      const added = missing.map((k) => `  ${JSON.stringify(k)}: ${JSON.stringify(pending[k])}`)
      lines.splice(closeIdx, 0, ...added)
    }
  }

  return { text: lines.join('\n'), missing }
}

/** 读文本文件；不存在或读不了时返回空串（首次使用时配置由 firekb init 生成） */
async function readTextFile(target: string): Promise<string> {
  try {
    return await fs.readFile(target, 'utf-8')
  } catch {
    return ''
  }
}

/** 宽松解析：配置暂时不可解析时不让整个界面报错 */
function parseJsonLoose(text: string): Record<string, any> {
  try {
    return JSON.parse(text) as Record<string, any>
  } catch {
    return {}
  }
}

async function readEnginesJson(): Promise<Record<string, any>> {
  return parseJsonLoose(await readTextFile(ENGINES_PATH))
}

// ---------------------------------------------------------------------------
// IPC Handlers
// ---------------------------------------------------------------------------

// 选择文件夹
ipcMain.handle('dialog:selectFolder', async () => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openDirectory']
  })
  if (result.canceled) {
    return null
  }
  return result.filePaths[0]
})

// 选择文件
ipcMain.handle('dialog:selectFiles', async () => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: 'Documents', extensions: ['pdf', 'pptx', 'epub'] },
      { name: 'All Files', extensions: ['*'] }
    ]
  })
  if (result.canceled) {
    return []
  }
  return result.filePaths
})

// 保存文件
ipcMain.handle('dialog:saveFile', async (_event, defaultPath: string) => {
  const result = await dialog.showSaveDialog(mainWindow!, {
    defaultPath,
    filters: [
      { name: 'Markdown', extensions: ['md'] },
      { name: 'Text', extensions: ['txt'] },
      { name: 'All Files', extensions: ['*'] }
    ]
  })
  if (result.canceled) {
    return null
  }
  return result.filePath
})

// 写文本文件（导出知识点等）
ipcMain.handle('file:writeText', async (_event, filePath: string, content: string) => {
  try {
    await fs.mkdir(path.dirname(filePath), { recursive: true })
    await fs.writeFile(filePath, content, 'utf-8')
    return { success: true, message: `已写入 ${filePath}` }
  } catch (error) {
    return { success: false, message: `写入失败：${(error as Error).message}` }
  }
})

// 获取项目状态
ipcMain.handle('firekb:getStatus', async () => {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_PATH, ['-m', 'firekb', 'status'], {
      cwd: FIREKB_ROOT,
      windowsHide: true,
      env: { ...process.env, FIREKB_ROOT, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
    })

    let stdout = ''
    let stderr = ''

    child.stdout.on('data', (data: Buffer) => {
      stdout += data.toString()
    })

    child.stderr.on('data', (data: Buffer) => {
      stderr += data.toString()
    })

    child.on('error', (err: Error) => {
      reject(new Error(err.message))
    })

    child.on('close', (code: number | null) => {
      if (code === 0) {
        resolve({ success: true, output: stdout })
      } else {
        reject(new Error(stderr || 'Command failed'))
      }
    })
  })
})

// 运行 FireKB 命令
ipcMain.handle('firekb:runCommand', async (_event, command: string, args: string[] = []) => {
  if (deploymentBusy()) throw new Error('环境配置正在运行，请等待完成')
  if (['extract', 'verify', 'skeleton', 'pipeline'].includes(command)) {
    const start = await runPython(['tools/device_setup.py', '--start'], 90000)
    if (start.code !== 0) throw new Error(start.stderr || '本地服务启动失败')
  }
  return new Promise((resolve) => {
    const child = spawn(PYTHON_PATH, ['-m', 'firekb', command, ...args], {
      cwd: FIREKB_ROOT,
      windowsHide: true,
      env: { ...process.env, FIREKB_ROOT, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
    })

    let stdout = ''
    let stderr = ''

    child.stdout.on('data', (data: Buffer) => {
      stdout += data.toString()
      mainWindow?.webContents.send('firekb:output', data.toString())
    })

    child.stderr.on('data', (data: Buffer) => {
      stderr += data.toString()
      mainWindow?.webContents.send('firekb:error', data.toString())
    })

    child.on('error', (err: Error) => {
      mainWindow?.webContents.send('firekb:error', err.message)
      resolve({ success: false, exitCode: -1, stdout, stderr: err.message })
    })

    child.on('close', (code: number | null) => {
      const exitCode = code === null ? -1 : code
      resolve({
        success: exitCode === 0,
        exitCode,
        stdout,
        stderr
      })
    })
  })
})

/**
 * 读取模型配置。
 *
 * 三份来源拼出一份视图，缺一不可：
 *   engines.json 的 _可用引擎   → 下拉框里能选什么
 *   engines.json 的 _出境策略   → 界面上要如实展示的合规边界
 *   engines --json 的实时探测   → 当前选择是否真的能用（缺依赖/需要网络）
 * 探测失败（Python 没装好、项目未初始化）时**仍然返回可编辑的配置**，只带上 probeError，
 * 否则用户会因为"环境没配好"而连配置文件都改不了。
 */
ipcMain.handle('models:getConfig', async () => {
  const cfg = await readEnginesJson()
  const available = (cfg['_可用引擎'] as Record<string, string[]>) || {}
  const policy = cfg['_出境策略'] as Record<string, unknown> | undefined
  const neverKeys = Object.keys(
    (policy?.['手动批准后可出站（个性化数据）'] as Record<string, string>) || {}
  )

  const envText = await readTextFile(ENV_PATH)
  const env = parseEnvText(envText)
  const apiKey = env['DEEPSEEK_API_KEY'] || ''

  const payload = {
    root: FIREKB_ROOT,
    enginesPath: ENGINES_PATH,
    envPath: ENV_PATH,
    engines: {
      parser: (cfg['parser'] as string) || '',
      ocr: (cfg['ocr'] as string) || '',
      asr: (cfg['asr'] as string) || ''
    },
    availableEngines: {
      parser: available['parser'] || [],
      ocr: available['ocr'] || [],
      asr: available['asr'] || []
    },
    engineStates: [] as EngineState[],
    egress: {
      entries: [] as EgressEntry[],
      anyEgress: false,
      consentFile: path.join(CONFIG_DIR, '.egress-consent.json'),
      notice: (cfg['_合规提示'] as string) || '',
      policy: neverKeys
    },
    api: {
      provider: env['FIREKB_PROVIDER'] || 'deepseek',
      apiKeyConfigured: Boolean(apiKey) && !apiKey.includes('替换'),
      apiKeyMasked: maskSecret(apiKey),
      baseUrl: env['DEEPSEEK_BASE_URL'] || '',
      model: env['DEEPSEEK_MODEL'] || '',
      modelStrong: env['DEEPSEEK_MODEL_STRONG'] || ''
    },
    probeError: '' as string
  }

  // 实时探测：engines_check 在缺依赖时会以退出码 1 结束，但 JSON 仍完整输出，
  // 因此这里只看 stdout 能不能解析，而不是看退出码。
  const probe = await runPython(['-m', 'firekb', 'engines', '--json'])
  try {
    const data = JSON.parse(probe.stdout) as Record<string, any>
    payload.engineStates = (data.engines || []).map((e: Record<string, any>): EngineState => ({
      kind: e.kind,
      name: e.name,
      label: e.spec?.label || '',
      missing: e.missing || [],
      pythonPkgs: e.spec?.python_pkgs || [],
      needsNetwork: Boolean(e.spec?.needs_network),
      needsGpu: Boolean(e.spec?.needs_gpu),
      egress: e.spec?.egress || 'none',
      note: e.spec?.note || ''
    }))
    payload.egress.entries = (data.egress || []).map((e: Record<string, any>): EgressEntry => ({
      kind: e.kind,
      engine: e.engine,
      label: e.label,
      egress: e.egress,
      meaning: e.meaning,
      needsNetwork: Boolean(e.needs_network)
    }))
    payload.egress.anyEgress = Boolean(data.any_egress)
    if (data.consent_file) payload.egress.consentFile = data.consent_file
  } catch {
    payload.probeError =
      (probe.stderr || '').trim().split('\n').slice(-3).join(' ') ||
      `无法执行 ${PYTHON_PATH} -m firekb engines --json（请检查 PYTHON_PATH 与 FIREKB_ROOT）`
  }

  return payload
})

/**
 * 保存模型配置。
 *
 * 三条写入纪律：
 *   1. **只改认识的键** —— engines.json 里大量 `_` 开头的注释键（出境策略、可用引擎、
 *      合规提示）是使用者的说明书，改配置不该把它们抹掉；
 *   2. **引擎名必须合法** —— 只接受 _可用引擎 里列出的实现，避免把流水线改成起不来的状态；
 *   3. **写前备份、写时原子** —— .env 先复制一份 .env.bak，两个文件都用临时文件改名落地。
 */
ipcMain.handle('models:setConfig', async (_event, patch: Record<string, any>): Promise<SaveResult> => {
  if (deploymentBusy()) return { success: false, message: '请等待环境配置完成' }
  const changed: string[] = []
  const enginesPatch = (patch?.engines || {}) as Record<string, string>
  const apiPatch = (patch?.api || {}) as Record<string, string>

  try {
    for (const value of Object.values(apiPatch)) {
      if (typeof value !== 'string' || /[\r\n]/.test(value)) throw new Error('配置值必须为单行文本')
    }
    if (apiPatch.baseUrl) {
      const endpoint = new URL(apiPatch.baseUrl)
      const local = ['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname)
      if ((!local && endpoint.protocol !== 'https:') || !['https:', 'http:'].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash)
        throw new Error('云端地址须为 HTTPS，地址不得包含凭据或查询参数')
    }
    if (apiPatch.provider && !['deepseek', 'dashscope', 'openrouter', 'custom', 'ollama'].includes(apiPatch.provider)) throw new Error('未知供应商')
    await fs.mkdir(CONFIG_DIR, { recursive: true })
    // ---- engines.json ----
    const engineKeys = Object.keys(enginesPatch).filter(
      (k) => (ENGINE_KINDS as readonly string[]).includes(k) && enginesPatch[k]
    )
    if (engineKeys.length) {
      const enginesText = await readTextFile(ENGINES_PATH)
      const available = (parseJsonLoose(enginesText)['_可用引擎'] as Record<string, string[]>) || {}
      const updates: Record<string, string> = {}
      for (const kind of engineKeys) {
        const name = enginesPatch[kind]
        const allowed = available[kind] || []
        if (allowed.length && !allowed.includes(name)) {
          return {
            success: false,
            message: `${kind} 不能设为「${name}」：可用实现只有 ${allowed.join('、')}`
          }
        }
        updates[kind] = name
        changed.push(`${kind}=${name}`)
      }
      const patched = patchJsonTopLevel(enginesText, updates)
      if (patched.missing.length) {
        return {
          success: false,
          message: `配置里没有这些键，拒绝凭空新建：${patched.missing.join('、')}`
        }
      }
      await writeFileAtomic(ENGINES_PATH, patched.text)
    }

    // ---- .env ----
    const envChanges: Record<string, string> = {}
    const keyMap: Record<string, string> = {
      provider: 'FIREKB_PROVIDER',
      baseUrl: 'DEEPSEEK_BASE_URL',
      model: 'DEEPSEEK_MODEL',
      modelStrong: 'DEEPSEEK_MODEL_STRONG',
      apiKey: 'DEEPSEEK_API_KEY'
    }
    for (const [field, envKey] of Object.entries(keyMap)) {
      // 白名单：只有 ENV_EDITABLE_KEYS 里的键允许被界面改写，其余键一概不碰
      if (!ENV_EDITABLE_KEYS.includes(envKey)) continue
      const value = apiPatch[field]
      if (typeof value === 'string' && value.trim()) {
        envChanges[envKey] = value.trim()
      }
    }

    if (apiPatch.baseUrl && !apiPatch.provider) {
      envChanges.FIREKB_PROVIDER = new URL(apiPatch.baseUrl).hostname === 'api.deepseek.com' ? 'deepseek' : 'custom'
    }
    if (envChanges.FIREKB_PROVIDER === 'ollama' && apiPatch.baseUrl && !['localhost', '127.0.0.1', '[::1]'].includes(new URL(apiPatch.baseUrl).hostname)) throw new Error('Ollama 档位只允许本机地址')
    let backup = ''
    if (Object.keys(envChanges).length) {
      const envText = await readTextFile(ENV_PATH)
      if (envText) {
        backup = `${ENV_PATH}.bak`
        await fs.writeFile(backup, envText, 'utf-8')
      }
      const before = parseEnvText(envText)
      for (const [k, v] of Object.entries(envChanges)) {
        if (before[k] !== v) changed.push(k)
      }
      await writeFileAtomic(ENV_PATH, updateEnvText(envText, envChanges))
    }

    if (!changed.length) {
      return { success: true, message: '没有需要改动的配置' }
    }
    return {
      success: true,
      message: `已保存：${changed.join('、')}`,
      enginesPath: ENGINES_PATH,
      envPath: ENV_PATH,
      backup: backup || undefined
    }
  } catch (error) {
    return { success: false, message: `保存失败：${(error as Error).message}` }
  }
})

// 读取课程列表
ipcMain.handle('firekb:getCourses', async () => {
  const coursesPath = path.join(FIREKB_ROOT, '00_配置', 'courses.json')
  const content = await fs.readFile(coursesPath, 'utf-8')
  const config = JSON.parse(content.replace(/^\uFEFF/, ''))
  const courses: { id: string; name: string; category?: 'course' | 'reference' }[] = Array.isArray(config) ? config : config.courses
  if (!Array.isArray(courses)) throw new Error('courses.json 缺少课程数组')
  // Include sources outside the timetable (e.g. regulations) without rewriting courses.json.
  const kpText = await readTextFile(path.join(FIREKB_ROOT, '30_知识点', 'kp.jsonl'))
  const names = new Set(courses.map(course => course.name))
  for (const line of kpText.replace(/^\uFEFF/, '').split('\n').filter(line => line.trim())) {
    const kp = JSON.parse(line)
    if (typeof kp.course === 'string' && !names.has(kp.course)) {
      courses.push({ id: `source:${kp.course}`, name: kp.course, category: 'reference' })
      names.add(kp.course)
    }
  }
  return courses
})

// 读取知识点
ipcMain.handle('firekb:getKnowledgePoints', async (_event, filters?: { course?: string, subject?: string }) => {
  const kpPath = path.join(FIREKB_ROOT, '30_知识点', 'kp.jsonl')
  const content = await fs.readFile(kpPath, 'utf-8')
  const lines = content.replace(/^\uFEFF/, '').split('\n').filter(line => line.trim())
  let kps = lines.map(line => JSON.parse(line))

  if (filters?.course) {
    kps = kps.filter(kp => kp.course === filters.course)
  }

  // Keep the Python data contract intact; adapt presentation fields at the IPC boundary.
  return kps.map(kp => ({ ...kp, title: kp.title ?? kp.name ?? '', content: kp.content ?? kp.definition ?? '' }))
})

ipcMain.handle('models:testConnection', async () => {
  if (deploymentBusy()) throw new Error('请等待环境配置完成')
  const start = await runPython(['tools/device_setup.py', '--start'], 90000)
  if (start.code !== 0) return { success: false, exitCode: start.code, stdout: '', stderr: start.stderr }
  const result = await runPython(['-u', 'tools/llm.py', '--selftest'], 180000)
  return { success: result.code === 0, exitCode: result.code, stdout: result.stdout, stderr: result.stderr }
})
