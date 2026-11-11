import { contextBridge, ipcRenderer, webUtils } from 'electron'
import type { ModelConfigPatch } from '../types/electron'

contextBridge.exposeInMainWorld('electronAPI', {
  intake: (action: string, data?: unknown) => ipcRenderer.invoke('intake:call', action, data),
  readTimetableFile: () => ipcRenderer.invoke('intake:readTimetable'),
  exportTimetable: (data: unknown, csv?: boolean) => ipcRenderer.invoke('intake:exportTimetable', data, csv),
  setAppearanceMode: (mode: 'light' | 'dark' | 'system') => ipcRenderer.invoke('appearance:setMode', mode),
  // 对话框
  selectFolder: () => ipcRenderer.invoke('dialog:selectFolder'),
  selectFiles: () => ipcRenderer.invoke('dialog:selectFiles'),
  getPathForFile: (file: File) => webUtils.getPathForFile(file),
  saveFile: (defaultPath: string) => ipcRenderer.invoke('dialog:saveFile', defaultPath),
  writeTextFile: (filePath: string, content: string) =>
    ipcRenderer.invoke('file:writeText', filePath, content),

  // FireKB 操作
  getStatus: () => ipcRenderer.invoke('firekb:getStatus'),
  importKnowledgeZip: () => ipcRenderer.invoke('firekb:importKnowledgeZip'),
  exportKnowledgeZip: (course: string, includeSources: boolean) => ipcRenderer.invoke('firekb:exportKnowledgeZip', course, includeSources),
  openOutput: (course?: string) => ipcRenderer.invoke('firekb:openOutput', course),
  openWebSource: (url: string) => ipcRenderer.invoke('firekb:openWebSource', url),
  runCommand: (command: string, args?: string[]) => ipcRenderer.invoke('firekb:runCommand', command, args),
  getCourses: () => ipcRenderer.invoke('firekb:getCourses'),
  chat: (id: string, data: unknown) => ipcRenderer.invoke('firekb:chat', id, data),
  cancelChat: (id: string) => ipcRenderer.invoke('firekb:chatCancel', id),
  manageKnowledge: (data: unknown) => ipcRenderer.invoke('firekb:chat', crypto.randomUUID(), { ...data as object, action: 'knowledge' }),
  inspectHumanReview: (kpId: string) => ipcRenderer.invoke('firekb:humanReview', 'inspect', kpId),
  approveHumanReview: (kpId: string, token: string) => ipcRenderer.invoke('firekb:humanReview', 'approve', kpId, token),
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
