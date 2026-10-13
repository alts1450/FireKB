import { ipcMain, dialog, BrowserWindow } from 'electron'
import { spawn } from 'child_process'
import type { DeploymentJob, DeploymentReport, ConsentStatus } from '../types/electron'

let job: DeploymentJob = { running: false, status: 'idle', logs: [] }
const profiles = ['speed', 'balanced', 'quality']
const categories = ['document', 'page_text', 'page_images', 'audio', 'transcript', 'personal']

export function registerDeployment(root: string, python: string, window: () => BrowserWindow | null) {
  const call = (args: string[], progress = false, timeout = 90000): Promise<{ success: boolean; stdout: string; stderr: string }> =>
    new Promise(resolve => {
      const child = spawn(python, ['-u', 'tools/device_setup.py', ...args], {
        cwd: root, windowsHide: true,
        env: { ...process.env, FIREKB_ROOT: root, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
      })
      let stdout = '', stderr = '', finished = false
      const timer = setTimeout(() => { child.kill(); finish(false, '配置超时；请检查日志和 setup.lock。') }, timeout)
      const finish = (success: boolean, error = '') => {
        if (finished) return
        finished = true
        clearTimeout(timer)
        resolve({ success, stdout, stderr: stderr + error })
      }
      const output = (data: Buffer, error: boolean) => {
        const text = data.toString('utf-8')
        if (error) stderr = (stderr + text).slice(-32768)
        else stdout = (stdout + text).slice(-262144)
        if (progress) {
          job.logs = [...job.logs, ...text.split(/\r?\n/).filter(Boolean)].slice(-100)
          window()?.webContents.send('deployment:progress', job)
        }
      }
      child.stdout.on('data', d => output(d, false))
      child.stderr.on('data', d => output(d, true))
      child.on('error', err => finish(false, `无法启动 Python：${err.message}`))
      child.on('close', code => finish(code === 0))
    })

  const consentStatus = async (): Promise<ConsentStatus> => {
    const r = await call(['--consent-status'])
    if (!r.success) throw new Error(r.stderr || '授权状态读取失败')
    return JSON.parse(r.stdout)
  }
  ipcMain.handle('deployment:inspect', async (): Promise<DeploymentReport> => {
    const result = await call(['--inspect'])
    if (!result.success) throw new Error(result.stderr || '检测失败，请确认 Python 路径')
    return JSON.parse(result.stdout)
  })
  ipcMain.handle('deployment:getJob', () => job)
  ipcMain.handle('deployment:install', async (_event, profile: unknown) => {
    if (typeof profile !== 'string' || !profiles.includes(profile)) throw new Error('非法档位')
    if (job.running) return { success: false, message: '已有配置任务正在执行' }
    // Reserve before awaiting the native dialog, preventing repeated clicks / multiple renderers.
    job = { running: true, status: 'confirming', profile, logs: [] }
    try {
      const win = window()
      if (!win) throw new Error('窗口已关闭')
      const choice = await dialog.showMessageBox(win, {
        type: 'question', title: '配置本地推理', buttons: ['开始配置', '取消'], defaultId: 1, cancelId: 1,
        message: '下载运行环境和所选模型，并在本机验证后启用',
        detail: `写入 ${root}\\.firekb-runtime。运行环境约 1.47GB，模型约 3.3–6.6GB；不会上传项目素材。使用固定官方版本并校验 SHA-256。`
      })
      if (choice.response !== 0) { job = { running: false, status: 'idle', logs: [] }; return { success: false, message: '已取消' } }
      job.status = 'running'
      window()?.webContents.send('deployment:progress', job)
      // Return immediately; the renderer can remount and recover the job state.
      void call(['--install', profile], true, 2 * 60 * 60 * 1000).then(result => {
        job.running = false
        job.status = result.success ? 'success' : 'error'
        job.message = result.success ? '本地环境已配置并通过加载检查' : result.stderr || '安装失败'
        window()?.webContents.send('deployment:progress', job)
      })
      return { success: true, message: '配置任务已开始' }
    } catch (error) {
      job = { ...job, running: false, status: 'error', message: String(error) }
      return { success: false, message: job.message }
    }
  })
  ipcMain.handle('deployment:start', async () => {
    if (job.running) throw new Error('请等待配置完成')
    return call(['--start'])
  })
  ipcMain.handle('consent:getStatus', consentStatus)
  ipcMain.handle('consent:approve', async (_event, request: unknown) => {
    const data = request as { categories?: unknown; note?: unknown }
    if (!data || !Array.isArray(data.categories) || !data.categories.length ||
      data.categories.some(c => typeof c !== 'string' || !categories.includes(c)) ||
      typeof data.note !== 'string' || !data.note.trim() || data.note.length > 1000) throw new Error('请选择数据类别并填写确认备注')
    const current = await consentStatus()
    if (current.local) throw new Error('当前为本机接口，无需批准云端发送；请先保存云端配置')
    const win = window()
    if (!win) throw new Error('窗口已关闭')
    const result = await dialog.showMessageBox(win, {
      title: '手动批准数据出站', type: 'question', buttons: ['批准所选范围', '取消'], defaultId: 1, cancelId: 1,
      message: `允许所选数据发送至 ${current.recipient}`,
      detail: `类别：${data.categories.join('、')}\n用途：知识处理。批准会持续保存在本机，直到撤销；更换接口地址须重新批准。\n请确认你有权上传相关素材。\n确认备注：${data.note}`
    })
    if (result.response !== 0) return { success: false, message: '已取消批准' }
    const latest = await consentStatus()
    if (latest.recipient !== current.recipient) throw new Error('接口配置已变化，请重新核对后批准')
    const r = await call(['--approve', ...data.categories as string[], '--note', data.note.trim(), '--expected-recipient', current.recipient])
    return { success: r.success, message: r.success ? r.stdout.trim() : r.stderr }
  })
  ipcMain.handle('consent:revoke', async () => {
    const r = await call(['--revoke'])
    return { success: r.success, message: r.success ? r.stdout.trim() : r.stderr }
  })
}

export function deploymentBusy() { return job.running }
