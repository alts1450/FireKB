export interface ElectronAPI {
  chat: (id: string, data: unknown) => Promise<ChatAnswer & ChatOptions>
  cancelChat: (id: string) => Promise<void>
  openWebSource: (url: string) => Promise<void>
  importKnowledgeZip: () => Promise<KnowledgeImportResult | null>
  exportKnowledgeZip: (course: string, includeSources: boolean) => Promise<KnowledgeZipResult | null>
  openOutput: (course?: string) => Promise<void>
  intake: <T = unknown>(action: import('./intake').IntakeAction, data?: unknown) => Promise<T>
  readTimetableFile: () => Promise<{ name: string; text: string } | null>
  exportTimetable: (data: unknown, csv?: boolean) => Promise<string | null>
  inspectDevice: () => Promise<DeploymentReport>
  getDeploymentJob: () => Promise<DeploymentJob>
  installDeployment: (profile: string) => Promise<SaveResult>
  startLocalService: () => Promise<{ success: boolean; stdout: string; stderr: string }>
  getConsentStatus: () => Promise<ConsentStatus>
  approveConsent: (request: { categories: EgressLevel[]; note: string }) => Promise<SaveResult>
  revokeConsent: () => Promise<SaveResult>
  testModelConnection: () => Promise<CommandResult>
  onDeploymentProgress: (callback: (job: DeploymentJob) => void) => () => void
  setAppearanceMode: (mode: 'light' | 'dark' | 'system') => Promise<void>
  selectFolder: () => Promise<string | null>
  selectFiles: () => Promise<string[]>
  getPathForFile: (file: File) => string
  saveFile: (defaultPath: string) => Promise<string | null>
  writeTextFile: (filePath: string, content: string) => Promise<WriteResult>
  getStatus: () => Promise<{ success: boolean; output: string }>
  runCommand: (command: string, args?: string[]) => Promise<CommandResult>
  getCourses: () => Promise<Course[]>
  getKnowledgePoints: (filters?: KPFilters) => Promise<KnowledgePoint[]>
  manageKnowledge: (data: unknown) => Promise<KnowledgeManagementResult>
  inspectHumanReview: (kpId: string) => Promise<HumanReviewDetail>
  approveHumanReview: (kpId: string, token: string) => Promise<Pick<KnowledgePoint, 'kp_id' | 'status' | 'verification_method' | 'human_review'>>
  getModelConfig: () => Promise<ModelConfigPayload>
  setModelConfig: (patch: ModelConfigPatch) => Promise<SaveResult>
  onOutput: (callback: (data: string) => void) => () => void
  onError: (callback: (data: string) => void) => () => void
}

export interface CommandResult {
  success: boolean
  exitCode: number
  stdout: string
  stderr: string
}

export interface WriteResult {
  success: boolean
  message: string
}

export interface Course {
  id: string
  name: string
  category?: 'course' | 'reference'
  chapters?: Chapter[]
}

export interface Chapter {
  num: number
  title: string
  page_start: number
  page_end: number
  kind: 'body' | 'appendix' | 'matter'
  sections?: Section[]
}

export interface Section {
  title: string
  page_start: number
  page_end: number
  node_id: string
}

export interface KnowledgePoint {
  kp_id: string
  course: string
  title: string
  content: string
  source_file: string
  page_label_start: string
  page_label_end: string
  importance: number
  status: 'verified' | 'pending' | 'rejected'
  verification_method?: string
  human_review?: { verdict: string; at: string; by: string; revision: string }
  source_type?: string
  note?: string
  ai_verification?: AiVerification
  ai_review_result?: AiVerification & { reason?: string; error?: string; retrieved_sources?: AiSource[] }
}

export interface AiSource { url: string; title: string }
export interface ChatOptions { models: string[]; defaultModel: string; efforts: string[] }
export interface ChatSegment { text: string; kind: 'normal' | 'cited' | 'unsupported'; citation?: { id: string; name: string; original: string; source: string; page: string; coverage_note?: string } }
export interface ChatAnswer { answer_id?: string; kp_id?: string; duplicate?: boolean; warning?: string; incomplete?: boolean; web_sources?: Array<{ title: string; url: string }>; web_error?: string; segments: ChatSegment[]; model: string; source_count: number; cited_source_count?: number }
export interface HumanReviewDetail {
  kp_id: string; name: string; definition: string; numbers: Array<string | number>; note: string
  source_file: string; source_text: string; token: string; status: string
  evidence: Array<{ quote: string; page_label: string }>
  issues: Array<{ type: string; detail: string }>
}
export interface AiVerification {
  policy?: string
  status: 'approved' | 'needs_human'; score: number; checked_at?: string; valid_until?: string
  scope?: string; sources?: AiSource[]; gates?: Record<string, boolean>
}

export interface KPFilters {
  course?: string
  subject?: string
}

/**
 * 模型与引擎配置
 * -----------------
 * 数据源是 FireKB 的两份既有配置，桌面端只做「读取 → 编辑 → 写回」，不另存一份：
 *   00_配置/engines.json  决定 parser / ocr / asr 三类能力各用哪台引擎
 *   00_配置/.env          LLM 接口地址与密钥（密钥只回传掩码，原文不出主进程）
 */

/** 引擎类别 —— 与 engines.json 的三个键一一对应 */
export type EngineKind = 'parser' | 'ocr' | 'asr'

/**
 * 出境等级（来自引擎的 spec.egress）。
 * none 表示数据不出本机；audio / transcript / personal 须手动批准后才可离开本机。
 */
export type EgressLevel =
  | 'none'
  | 'document'
  | 'page_text'
  | 'page_images'
  | 'audio'
  | 'transcript'
  | 'personal'

/** 单台引擎的当前状态（来自 `python -m firekb engines --json`） */
export interface EngineState {
  kind: EngineKind
  name: string
  label: string
  /** 缺失的依赖（空数组＝可用） */
  missing: string[]
  pythonPkgs: string[]
  needsNetwork: boolean
  needsGpu: boolean
  egress: EgressLevel
  note: string
}

/** 出境条目（engines --json 的 egress 汇总） */
export interface EgressEntry {
  kind: EngineKind
  engine: string
  label: string
  egress: EgressLevel
  meaning: string
  needsNetwork: boolean
}

/** API 配置（对应 .env 的四个键；密钥只以掩码形式回传） */
export interface ApiConfig {
  provider: string
  apiKeyConfigured: boolean
  apiKeyMasked: string
  baseUrl: string
  model: string
  modelStrong: string
}

/** 模型配置页的完整载荷 */
export interface ModelConfigPayload {
  /** FireKB 项目根目录 */
  root: string
  enginesPath: string
  envPath: string
  /** 三类引擎当前选中的实现 */
  engines: Record<EngineKind, string>
  /** 每类的可选实现（来自 engines.json 的 _可用引擎） */
  availableEngines: Record<EngineKind, string[]>
  engineStates: EngineState[]
  egress: {
    entries: EgressEntry[]
    anyEgress: boolean
    consentFile: string
    /** engines.json 的 _合规提示 */
    notice: string
    /** 须手动批准的数据类型 */
    policy: string[]
  }
  api: ApiConfig
  /** Python 探测失败时的原因（此时仍允许编辑与保存） */
  probeError?: string
}

/** 保存请求：只提交用户改动过的字段 */
export interface ModelConfigPatch {
  engines?: Partial<Record<EngineKind, string>>
  api?: {
    provider?: string
    baseUrl?: string
    model?: string
    modelStrong?: string
    /** 仅在用户明确改了密钥时才带上 */
    apiKey?: string
  }
}

/** 保存结果 */
export interface SaveResult {
  success: boolean
  message: string
  enginesPath?: string
  envPath?: string
  /** 写 .env 前留下的备份路径（便于回退） */
  backup?: string
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}

export interface DeploymentDevice {
  os: string; arch: string; cpu: { name: string; cores: number | null; threads: number }
  gpus: { index: number; name: string; vramGb: number; freeVramGb: number; driver: string }[]
  ramGb: number; freeRamGb: number; freeDiskGb: number; python: string; warnings: string[]; detectedAt: string
}
export interface DeploymentProfile {
  id: string; title: string; model: string; tag: string; quant: string; vram: number; ram: number; disk: number
  context: number; description: string; eligible: boolean; route: 'local' | 'cloud'; reasons: string[]
  command: string; runCommand: string
}
export interface DeploymentReport {
  device: DeploymentDevice; baselineMet: boolean; baselineReasons: string[]; profiles: DeploymentProfile[]
  runtimeVersion: string; endpoint: string; resourceEstimates: boolean; scope: string
}
export interface DeploymentJob {
  running: boolean; status: 'idle' | 'confirming' | 'running' | 'success' | 'error'; logs: string[]
  message?: string; profile?: string
}
export interface ConsentStatus {
  recipient: string; purpose: string; local: boolean; approved: EgressLevel[]
}

export interface KnowledgeDraft { name: string; definition: string; note: string; numbers: Array<string | number>; importance: number; chapter: string; section: string | null }
export interface KnowledgeManagementResult { point?: KnowledgeDraft; token?: string; ids?: string[]; delete_token?: string; names?: string[]; deleted?: string[]; backup?: string; unchanged?: boolean }

export interface KnowledgeZipResult {
  path: string
  course_count: number
  point_count: number
  source_count: number
  missing_count: number
}

export interface KnowledgeImportResult {
  added: number
  skipped: number
  source_count: number
  source_conflicts: number
  pending_count: number
}
