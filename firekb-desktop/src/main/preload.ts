import { contextBridge, ipcRenderer } from 'electron'
import type { ModelConfigPatch } from '../types/electron'

contextBridge.exposeInMainWorld('electronAPI', {
  setAppearanceMode: (mode: 'light' | 'dark' | 'system') => ipcRenderer.invoke('appearance:setMode', mode),
  // 对话框
  selectFolder: () => ipcRenderer.invoke('dialog:selectFolder'),
  selectFiles: () => ipcRenderer.invoke('dialog:selectFiles'),
  saveFile: (defaultPath: string) => ipcRenderer.invoke('dialog:saveFile', defaultPath),
  writeTextFile: (filePath: string, content: string) =>
    ipcRenderer.invoke('file:writeText', filePath, content),

  // FireKB 操作
  getStatus: () => ipcRenderer.invoke('firekb:getStatus'),
  runCommand: (command: string, args?: string[]) => ipcRenderer.invoke('firekb:runCommand', command, args),
  getCourses: () => ipcRenderer.invoke('firekb:getCourses'),
  getKnowledgePoints: (filters?: { course?: string, subject?: string }) =>
    ipcRenderer.invoke('firekb:getKnowledgePoints', filters),

  // 模型与引擎配置
  inspectDevice: () => ipcRenderer.invoke('deployment:inspect'),
  getDeploymentJob: () => ipcRenderer.invoke('deployment:getJob'),
  installDeployment: (profile: string) => ipcRenderer.invoke('deployment:install', profile),
  startLocalService: () => ipcRenderer.invoke('deployment:start'),
  getConsentStatus: () => ipcRenderer.invoke('consent:getStatus'),
  approveConsent: (request: { categories: string[]; note: string }) => ipcRenderer.invoke('consent:approve', request),
  revokeConsent: () => ipcRenderer.invoke('consent:revoke'),
  testModelConnection: () => ipcRenderer.invoke('models:testConnection'),
  onDeploymentProgress: (callback: (data: import('../types/electron').DeploymentJob) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, data: import('../types/electron').DeploymentJob) => callback(data)
    ipcRenderer.on('deployment:progress', listener)
    return () => ipcRenderer.removeListener('deployment:progress', listener)
  },
  getModelConfig: () => ipcRenderer.invoke('models:getConfig'),
  setModelConfig: (patch: ModelConfigPatch) => ipcRenderer.invoke('models:setConfig', patch),

  // 输出监听
  onOutput: (callback: (data: string) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, data: string) => callback(data)
    ipcRenderer.on('firekb:output', listener)
    return () => ipcRenderer.removeListener('firekb:output', listener)
  },
  onError: (callback: (data: string) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, data: string) => callback(data)
    ipcRenderer.on('firekb:error', listener)
    return () => ipcRenderer.removeListener('firekb:error', listener)
  }
})
