import Icon from './Icon'
import DeploymentPanel from './DeploymentPanel'
import { useState, useEffect } from 'react'
import './ModelConfig.css'
import type {
  EngineKind,
  EgressLevel,
  ModelConfigPatch,
  ModelConfigPayload
} from '../../types/electron'

// 三类引擎的展示文案（与 engines.json 的三个键一一对应）
const ENGINE_META: { kind: EngineKind; title: string; subtitle: string }[] = [
  { kind: 'parser', title: '解析引擎', subtitle: '把 PDF / PPTX / EPUB 变成页级文本（M1 解析）' },
  { kind: 'ocr', title: 'OCR 引擎', subtitle: '识别影印版教材的页面图像（M2 识别）' },
  { kind: 'asr', title: '转写引擎', subtitle: '课堂录音转文字（M3 转写；只作线索，永不作为引用来源）' }
]

// 出境等级的人话解释。none 之外都意味着数据离开本机，界面上必须说清楚。
const EGRESS_LABEL: Record<EgressLevel, string> = {
  none: '本机执行，数据不出境',
  document: '会把原始文件发往第三方',
  page_text: '会把页级文本发往第三方',
  page_images: '会把页面图像发往第三方',
  audio: '会把录音发往第三方（须手动批准）',
  transcript: '会把转写文本发往第三方（须手动批准）',
  personal: '会把学习痕迹发往第三方（须手动批准）'
}

const EMPTY: ModelConfigPayload = {
  root: '',
  enginesPath: '',
  envPath: '',
  engines: { parser: '', ocr: '', asr: '' },
  availableEngines: { parser: [], ocr: [], asr: [] },
  engineStates: [],
  egress: { entries: [], anyEgress: false, consentFile: '', notice: '', policy: [] },
  api: { provider: 'deepseek', apiKeyConfigured: false, apiKeyMasked: '', baseUrl: '', model: '', modelStrong: '' }
}

export default function ModelConfig() {
  const [config, setConfig] = useState<ModelConfigPayload>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<{ kind: 'ok' | 'error' | 'info'; text: string }>({
    kind: 'info',
    text: ''
  })

  // 草稿：只装用户改动过的字段，保存时按「补丁」提交，避免把整份配置覆盖回去
  const [engineDraft, setEngineDraft] = useState<Partial<Record<EngineKind, string>>>({})
  const [apiDraft, setApiDraft] = useState<{
    provider?: string
    baseUrl?: string
    model?: string
    modelStrong?: string
    apiKey?: string
  }>({})
  const [revision, setRevision] = useState(0)
  const [testing, setTesting] = useState(false)
  const [keyEditing, setKeyEditing] = useState(false)

  useEffect(() => {
    void loadConfig()
  }, [])

  const loadConfig = async (options?: { silent?: boolean }) => {
    setLoading(true)
    try {
      const data = await window.electronAPI.getModelConfig()
      setConfig(data)
      setRevision(r => r + 1)
      setEngineDraft({})
      setApiDraft({})
      setKeyEditing(false)
      // 保存之后重新读取时不要覆盖提示：否则"已保存"会被"已读取当前配置"顶掉，
      // 使用者根本看不到自己那一次操作的结果。
      if (!options?.silent) {
        setStatus(
          data.probeError
            ? { kind: 'error', text: `引擎状态探测失败：${data.probeError}` }
            : { kind: 'ok', text: '已读取当前配置' }
        )
      }
    } catch (error) {
      setStatus({ kind: 'error', text: `读取失败：${String(error)}` })
    } finally {
      setLoading(false)
    }
  }

  const dirty =
    Object.keys(engineDraft).length > 0 ||
    Object.keys(apiDraft).some((k) => apiDraft[k as keyof typeof apiDraft] !== undefined)

  const handleSave = async () => {
    if (!dirty) {
      setStatus({ kind: 'info', text: '没有需要保存的改动' })
      return
    }
    setSaving(true)
    try {
      const patch: ModelConfigPatch = {}
      if (Object.keys(engineDraft).length) patch.engines = engineDraft
      if (Object.keys(apiDraft).length) patch.api = apiDraft

      const result = await window.electronAPI.setModelConfig(patch)
      if (result.success) {
        setStatus({ kind: 'ok', text: result.message })
        await loadConfig({ silent: true })
      } else {
        setStatus({ kind: 'error', text: result.message })
      }
    } catch (error) {
      setStatus({ kind: 'error', text: `保存失败：${String(error)}` })
    } finally {
      setSaving(false)
    }
  }

  /** 取某类引擎的当前状态（探测失败时为空） */
  const stateOf = (kind: EngineKind) => config.engineStates.find((e) => e.kind === kind)
  const currentEngine = (kind: EngineKind) => engineDraft[kind] ?? config.engines[kind]

  return (
    <div className="model-config">
      <div className="config-notice">
        <div className="notice-icon"><Icon name="settings" size={20} /></div>
        <div className="notice-content">
          <h3 className="notice-title">引擎与模型配置</h3>
          <p className="notice-text">选择适合本机的处理引擎，配置知识抽取模型的连接信息。下方显示当前依赖状态及数据是否需要发送至第三方。</p>
          {status.text && (
            <p className={`notice-status notice-status-${status.kind}`} role="status">{status.text}</p>
          )}
        </div>
      </div>

      <DeploymentPanel revision={revision} onConfigured={() => void loadConfig()} />

      <div className="config-sections">
        {ENGINE_META.map(({ kind, title, subtitle }) => {
          const st = stateOf(kind)
          const options = config.availableEngines[kind]
          const value = currentEngine(kind)
          const changed = engineDraft[kind] !== undefined
          return (
            <div className="config-section" key={kind}>
              <h3 className="section-title">
                {title}
                <span className="section-tag">{kind}</span>
              </h3>
              <p className="section-subtitle">{subtitle}</p>

              <div className="config-field">
                <label className="field-label" htmlFor={`engine-${kind}`}>实现</label>
                <select
                  id={`engine-${kind}`}
                  className="field-input"
                  value={value}
                  disabled={loading || saving}
                  onChange={(e) => setEngineDraft((d) => ({ ...d, [kind]: e.target.value }))}
                >
                  {options.length === 0 && <option value={value}>{value || '（配置文件中未列出可选实现）'}</option>}
                  {options.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
                {changed && <span className="field-flag">已改动，保存后生效</span>}
              </div>

              {st && (
                <>
                  <div className="engine-status">
                    <span className={st.missing.length ? 'badge badge-warn' : 'badge badge-ok'}>
                      {st.missing.length ? `缺少 ${st.missing.length} 项依赖` : '依赖齐备'}
                    </span>
                    <span className={`badge ${st.egress === 'none' ? 'badge-ok' : 'badge-warn'}`}>
                      {EGRESS_LABEL[st.egress] || st.egress}
                    </span>
                    {st.needsNetwork && <span className="badge badge-warn">需要联网</span>}
                    {st.needsGpu && <span className="badge badge-warn">需要 GPU</span>}
                  </div>
                  {st.missing.length > 0 && (
                    <ul className="field-missing">
                      {st.missing.map((m) => <li key={m}>{m}</li>)}
                    </ul>
                  )}
                  <p className="field-hint">{st.label}</p>
                  {st.note && <p className="field-note">{st.note}</p>}
                </>
              )}
              {!st && (
                <p className="field-hint">
                  未能探测该引擎的运行状态（Python 环境或项目初始化未就绪），仍可修改配置后保存。
                </p>
              )}
            </div>
          )
        })}
      </div>

      <div className="config-section">
        <h3 className="section-title">
          本地与云端文本接口
          <span className="section-tag">.env</span>
        </h3>
        <p className="section-subtitle">
          用于知识点抽取与校验。接口连接测试只发送固定测试句；生产素材须按实际数据类别手动批准。
        </p>

        <div className="config-field">
          <label className="field-label" htmlFor="api-provider">服务平台</label>
          <select id="api-provider" className="field-input" value={apiDraft.provider ?? config.api.provider} disabled={loading || saving} onChange={e => {
            const provider = e.target.value
            const presets: Record<string, { baseUrl: string; model: string }> = {
              dashscope: { baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen3.8-max-0902' },
              deepseek: { baseUrl: 'https://api.deepseek.com', model: 'deepseek-v4-pro' },
              openrouter: { baseUrl: 'https://openrouter.ai/api/v1', model: 'deepseek/deepseek-v4.1-flash' },
              ollama: { baseUrl: 'http://127.0.0.1:11435/v1', model: 'firekb-balanced' }
            }
            const preset = presets[provider]
            setApiDraft(d => ({ ...d, provider, ...(preset ? { ...preset, modelStrong: preset.model } : {}) }))
          }}>
            <option value="dashscope">阿里云百炼 · 中文任务优先</option><option value="deepseek">DeepSeek</option><option value="openrouter">OpenRouter</option><option value="custom">自定义兼容 API</option><option value="ollama">Ollama · 本机</option>
          </select>
          <p className="field-hint">预设只填写连接信息。请核对账户、区域、模型可用性与服务条款，保存后测试连接；配置不会批准上传项目数据。</p>
        </div>
        <div className="config-field">
          <label className="field-label" htmlFor="api-key">API Key</label>
          {!keyEditing ? (
            <div className="field-row">
              <input
                id="api-key"
                className="field-input"
                value={config.api.apiKeyConfigured ? config.api.apiKeyMasked : '（未配置）'}
                readOnly
              />
              <button className="btn btn-secondary" onClick={() => setKeyEditing(true)} disabled={loading || saving}>
                更换
              </button>
            </div>
          ) : (
            <div className="field-row">
              <input
                id="api-key"
                className="field-input"
                type="password"
                placeholder="粘贴新的 Key（留空则不修改）"
                autoComplete="off"
                onChange={(e) => setApiDraft((d) => ({ ...d, apiKey: e.target.value }))}
              />
              <button className="btn btn-secondary" onClick={() => { setKeyEditing(false); setApiDraft((d) => ({ ...d, apiKey: undefined })) }}>
                取消
              </button>
            </div>
          )}
          <p className="field-hint">密钥只在主进程内读写，界面上只显示掩码；留空即保持原值不变。</p>
        </div>

        <div className="config-field">
          <label className="field-label" htmlFor="api-base">接口地址</label>
          <input
            id="api-base"
            className="field-input"
            value={apiDraft.baseUrl ?? config.api.baseUrl}
            disabled={loading || saving}
            onChange={(e) => setApiDraft((d) => ({ ...d, baseUrl: e.target.value }))}
          />
        </div>

        <div className="config-grid">
          <div className="config-field">
            <label className="field-label" htmlFor="api-model">默认模型</label>
            <input
              id="api-model"
              className="field-input"
              value={apiDraft.model ?? config.api.model}
              disabled={loading || saving}
              onChange={(e) => setApiDraft((d) => ({ ...d, model: e.target.value }))}
            />
            <p className="field-hint">日常抽取用它</p>
          </div>
          <div className="config-field">
            <label className="field-label" htmlFor="api-model-strong">强模型</label>
            <input
              id="api-model-strong"
              className="field-input"
              value={apiDraft.modelStrong ?? config.api.modelStrong}
              disabled={loading || saving}
              onChange={(e) => setApiDraft((d) => ({ ...d, modelStrong: e.target.value }))}
            />
            <p className="field-hint">仅在关键节点临时切换</p>
          </div>
        </div>
      </div>

      <div className="config-section">
        <h3 className="section-title">
          数据出境
          <span className="section-tag">合规边界</span>
        </h3>
        <p className="section-subtitle">
          {config.egress.anyEgress
            ? '当前有引擎会把数据送出本机 —— 首次使用前需确认你有权上传相应素材。'
            : '当前解析、OCR 和转写引擎在本机执行；文本模型的数据发送由上方批准范围控制。'}
        </p>

        {config.egress.entries.length > 0 && (
          <ul className="egress-list">
            {config.egress.entries.map((e) => (
              <li key={`${e.kind}-${e.engine}`}>
                <span className={`badge ${e.egress === 'none' ? 'badge-ok' : 'badge-warn'}`}>{e.egress}</span>
                <span className="egress-name">{e.kind} · {e.engine}</span>
                <span className="egress-meaning">{e.meaning}</span>
              </li>
            ))}
          </ul>
        )}

        {config.egress.policy.length > 0 && (
          <div className="policy-box">
            <p className="policy-title">以下个性化数据须手动批准后才能发送</p>
            <ul className="policy-list">
              {config.egress.policy.map((p) => <li key={p}><code>{p}</code></li>)}
            </ul>
          </div>
        )}

        {config.egress.notice && <p className="field-note">{config.egress.notice}</p>}
      </div>

      <div className="config-actions">
        <button className="btn btn-secondary" disabled={loading || saving || testing || dirty} onClick={async () => {
          setTesting(true)
          try {
            const r = await window.electronAPI.testModelConnection()
            setStatus({ kind: r.success ? 'ok' : 'error', text: r.success ? '连接及 JSON 输出测试通过' : r.stderr || r.stdout })
          } catch (e) { setStatus({ kind: 'error', text: String(e) }) }
          finally { setTesting(false) }
        }}>{testing ? '测试中…' : '测试已保存接口'}</button>
        <button className="btn btn-secondary" onClick={() => void loadConfig()} disabled={loading || saving}>
          重新读取
        </button>
        <button className="btn btn-primary" onClick={handleSave} disabled={!dirty || loading || saving}>
          {saving ? '保存中…' : '保存配置'}
        </button>
      </div>

      <div className="config-footer">
        <h4 className="footer-title">配置文件位置</h4>
        <ul className="footer-list">
          <li>引擎选择：<code>{config.enginesPath || '00_配置/engines.json'}</code></li>
          <li>接口与密钥：<code>{config.envPath || '00_配置/.env'}</code></li>
          <li>出境同意记录：<code>{config.egress.consentFile || '00_配置/.egress-consent.json'}</code></li>
          <li>项目根目录：<code>{config.root || '（未设置 FIREKB_ROOT）'}</code></li>
        </ul>
      </div>
    </div>
  )
}
