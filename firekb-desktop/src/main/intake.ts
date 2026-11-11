import { ipcMain, dialog, BrowserWindow } from 'electron'
import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs/promises'
import type { IntakeAction } from '../types/intake'

export function registerIntake(root: string, python: string, window: () => BrowserWindow | null) {
  let writing = false
  ipcMain.handle('intake:call', async (_event, action: IntakeAction, data: unknown = {}) => {
    if (!['state','save','parse','preview','import'].includes(action)) throw new Error('未知归课操作')
    const mutating = action === 'save' || action === 'import'
    if (mutating && writing) throw new Error('归档或保存正在进行，请稍后重试')
    if (mutating) writing = true
    try {
      return await new Promise((resolve, reject) => {
        const child = spawn(python, ['-B','-u',path.join(root,'tools','intake_bridge.py')], {cwd:root,windowsHide:true,env:{...process.env,FIREKB_ROOT:root,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}})
        child.stdout.setEncoding('utf8')
        child.stderr.setEncoding('utf8')
        let stdout = '', stderr = ''
        const timer = setTimeout(() => { child.kill(); reject(new Error('归课操作超时，可重试未完成文件')) }, 30*60*1000)
        child.stdout.on('data', chunk => { stdout += chunk.toString() })
        child.stderr.on('data', chunk => { stderr += chunk.toString() })
        child.on('error', error => { clearTimeout(timer); reject(error) })
        child.on('close', () => {
          clearTimeout(timer)
          try { const result = JSON.parse(stdout); if (!result.success) reject(new Error(result.error)); else resolve(result.data) }
          catch { reject(new Error(stderr || '归课模块未返回可用结果')) }
        })
        child.stdin.on('error', () => undefined)
        child.stdin.end(JSON.stringify({action,data}))
      })
    } finally { if (mutating) writing = false }
  })
  ipcMain.handle('intake:readTimetable', async () => {
    const result = await dialog.showOpenDialog(window()!, {properties:['openFile'],filters:[{name:'课表',extensions:['csv','json','ics']}]})
    if (result.canceled) return null
    const file = result.filePaths[0]
    if ((await fs.stat(file)).size > 4*1024*1024) throw new Error('课表文件超过 4 MB')
    return {name:path.basename(file),text:await fs.readFile(file,'utf-8')}
  })
  ipcMain.handle('intake:exportTimetable', async (_event, data: unknown, csv = false) => {
    const result = await dialog.showSaveDialog(window()!, {defaultPath:csv?'FireKB课表模板.csv':'FireKB课表.json',filters:[{name:csv?'CSV':'JSON',extensions:[csv?'csv':'json']}]})
    if (result.canceled || !result.filePath) return null
    await fs.writeFile(result.filePath,csv ? String(data) : JSON.stringify(data,null,2),'utf-8')
    return result.filePath
  })
}
