import { useEffect, useState } from 'react'
import type { ConsentStatus, DeploymentJob, DeploymentReport, EgressLevel } from '../../types/electron'
import './DeploymentPanel.css'

const CATEGORY_LABELS: [EgressLevel, string][] = [
  ['page_text', '教材页级文本'], ['page_images', '页面图像'], ['document', '原始文件'],
  ['audio', '课堂录音'], ['transcript', '转写文本'], ['personal', '笔记、错题与学习记录']
]

export default function DeploymentPanel({ revision, onConfigured }: { revision: number; onConfigured: () => void }) {
  const [report, setReport] = useState<DeploymentReport>()
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState('balanced')
  const [job, setJob] = useState<DeploymentJob>({ running: false, status: 'idle', logs: [] })
  const [consent, setConsent] = useState<ConsentStatus>()
  const [approved, setApproved] = useState<EgressLevel[]>([])
  const [note, setNote] = useState('')
  const [consentBusy, setConsentBusy] = useState(false)
  const [message, setMessage] = useState('')
  const inspect = async () => {
    setLoading(true)
    try { setReport(await window.electronAPI.inspectDevice()) }
    catch (error) { setMessage(`设备检测失败：${String(error)}`) }
    finally { setLoading(false) }
  }
  useEffect(() => {
    void inspect()
    void window.electronAPI.getDeploymentJob().then(setJob).catch(e => setMessage(String(e)))
    return window.electronAPI.onDeploymentProgress(next => {
      setJob(next)
      if (next.status === 'success') onConfigured()
    })
  }, [])
  useEffect(() => {
    setApproved([])
    setNote('')
    void window.electronAPI.getConsentStatus().then(setConsent).catch(e => setMessage(String(e)))
  }, [revision])
  const profile = report?.profiles.find(p => p.id === selected)
  const install = async () => {
    try {
      const result = await window.electronAPI.installDeployment(selected)
      setMessage(result.message)
      setJob(await window.electronAPI.getDeploymentJob())
    } catch (error) { setMessage(String(error)) }
  }
  const authorize = async (revoke = false) => {
    setConsentBusy(true)
    try {
      const result = revoke ? await window.electronAPI.revokeConsent() : await window.electronAPI.approveConsent({ categories: approved, note })
      setMessage(result.message)
      setConsent(await window.electronAPI.getConsentStatus())
      if (result.success) { setApproved([]); setNote('') }
    } catch (error) { setMessage(String(error)) }
    finally { setConsentBusy(false) }
  }
  return <>
    <section className="config-section deployment-panel" aria-label="设备与部署">
      <div className="deployment-heading">
        <div><h3 className="section-title">为这台设备配置模型</h3><p className="section-subtitle">检测本机资源，选择运行档位。低于 4060 8GB / 13700F / 16GB 时优先推荐云端。</p></div>
        <button className="btn btn-secondary" disabled={loading || job.running} onClick={() => void inspect()}>{loading ? '检测中…' : '重新检测'}</button>
      </div>
      {report && <>
        <dl className="device-facts">
          <div><dt>处理器</dt><dd>{report.device.cpu.name}<small>{report.device.cpu.cores ?? '?'} 核 / {report.device.cpu.threads} 线程</small></dd></div>
          <div><dt>显卡</dt><dd>{report.device.gpus.map(g => `${g.name} · ${g.vramGb}GB`).join(' / ') || '未检出 NVIDIA 专用显存'}<small>{report.device.gpus.map(g => `可用 ${g.freeVramGb}GB`).join(' / ') || '驱动信息不完整'}</small></dd></div>
          <div><dt>系统内存</dt><dd>{report.device.ramGb}GB<small>当前可用 {report.device.freeRamGb}GB</small></dd></div>
          <div><dt>项目磁盘</dt><dd>剩余 {report.device.freeDiskGb}GB<small>{report.device.os} / {report.device.arch}</small></dd></div>
        </dl>
        <div className="deployment-tiers" role="radiogroup" aria-label="推理档位">
          {report.profiles.map(p => <label key={p.id} className={`deployment-tier ${selected === p.id ? 'selected' : ''}`}>
            <input type="radio" name="deployment-tier" checked={selected === p.id} onChange={() => setSelected(p.id)} disabled={job.running} />
            <span className="tier-title">{p.title}</span><span className="tier-model">{p.model}</span><span className="tier-meta">{p.quant} · {p.context.toLocaleString()} 上下文</span>
            <span className={`tier-route ${p.eligible ? 'local' : ''}`}>{p.eligible ? '可试部署到本机' : '推荐云端推理'}</span>
          </label>)}
        </div>
        {profile && <div className="deployment-detail">
          <p>{profile.description}</p>
          <p className="field-hint">预留资源：显存 {profile.vram}GB / 内存 {profile.ram}GB / 磁盘 {profile.disk}GB。以文本、单并发为起点，资源数据为估算。</p>
          {profile.reasons.length > 0 && <ul className="deployment-reasons">{profile.reasons.map(r => <li key={r}>{r}</li>)}</ul>}
          <div className="deployment-buttons">
            <button className="btn btn-primary" disabled={!profile.eligible || job.running || loading} onClick={() => void install()}>{job.running ? '配置中…' : '一键配置本地环境'}</button>
            <button className="btn btn-secondary" disabled={job.running} onClick={() => document.getElementById('api-base')?.focus()}>配置云端 API</button>
            <button className="btn btn-text" disabled={job.running} onClick={() => { void window.electronAPI.startLocalService().then(r => setMessage(r.success ? '本地服务已就绪；云端配置时无需启动本地服务。' : r.stderr)).catch(e => setMessage(String(e))) }}>启动已安装服务</button>
          </div>
          <details className="deployment-commands"><summary>命令行部署与运行指令</summary><p className="field-hint">在项目根运行。服务仅监听本机 11435 端口；命令行终端需设置 OLLAMA_HOST。</p>
            <pre>{profile.command}{'\n'}python tools/device_setup.py --start{'\n'}$env:OLLAMA_HOST = '127.0.0.1:11435'{'\n'}{profile.runCommand}</pre>
            <button className="btn btn-text" onClick={() => { void navigator.clipboard.writeText(`${profile.command}\npython tools/device_setup.py --start\n$env:OLLAMA_HOST = '127.0.0.1:11435'\n${profile.runCommand}`).then(() => setMessage('指令已复制')).catch(() => setMessage('复制失败，请手动选择指令')) }}>复制指令</button>
          </details>
        </div>}
        {report.device.warnings.length > 0 && <p className="field-hint">{report.device.warnings.join(' ')}</p>}
      </>}
      {(job.logs.length > 0 || job.message) && <div className="deployment-progress" aria-label="配置进度"><p role="status">{job.message || '正在配置，请保持应用开启。'}</p><pre>{job.logs.slice(-15).join('\n')}</pre></div>}
      {message && <p className="notice-status notice-status-info" role="status">{message}</p>}
    </section>
    <section className="config-section" aria-label="手动出站批准">
      <h3 className="section-title">数据离开本机前，由你批准</h3>
      <p className="section-subtitle">配置云端接口不等于批准上传。选择可发送的数据类别，填写用途备注，再手动确认；可以随时撤销，之后的新请求将被拦截。</p>
      <p className="consent-recipient">当前接收方：<code>{consent?.recipient || '读取中…'}</code></p>
      {consent?.local ? <p className="field-hint">当前接口在本机运行。保存云端地址后可设置对应批准范围。</p> : <>
        <fieldset className="consent-categories" disabled={consentBusy || !consent || job.running}><legend>允许发送的类别（默认不勾选）</legend>
          {CATEGORY_LABELS.map(([id, label]) => <label key={id}><input type="checkbox" checked={approved.includes(id)} onChange={e => setApproved(current => e.target.checked ? [...current, id] : current.filter(c => c !== id))} />{label}{consent?.approved.includes(id) && <span>已批准</span>}</label>)}
        </fieldset>
        <label className="field-label" htmlFor="consent-note">确认备注</label><input id="consent-note" className="field-input" value={note} maxLength={1000} placeholder="例如：用于本人复习整理，已确认有权上传" onChange={e => setNote(e.target.value)} disabled={consentBusy || job.running} />
      </>}
      <div className="deployment-buttons">
        <button className="btn btn-primary" disabled={consentBusy || job.running || !consent || consent.local || !approved.length || !note.trim()} onClick={() => void authorize()}>手动批准所选范围</button>
        <button className="btn btn-secondary" disabled={consentBusy || job.running} onClick={() => void authorize(true)}>撤销全部批准</button>
      </div>
      <p className="field-hint">批准绑定当前接口地址、类别和知识处理用途；切换服务须重新批准。授权与审计仅保存在本机，不随 Git 同步。不得上传未取得处理权利的第三方素材。</p>
    </section>
  </>
}
