import { useState, useEffect, useRef } from 'react'
import './OutputPanel.css'
import Icon from './Icon'
import MathText from './MathText'
import KnowledgeActions from './KnowledgeActions'
import type { Course, KnowledgePoint, HumanReviewDetail } from '../../types/electron'

interface OutputPanelProps {
  selectedCourse: string
  courses: Course[]
  onCoursesChanged: () => Promise<void>
  active: boolean
  isProcessing: boolean
  onProcessingChange: (processing: boolean) => void
}
const PAGE_SIZE = 40
const statusLabels = { verified: '已验证', pending: '待确认', rejected: '已拒绝' }
const statusColors = { verified: 'var(--success)', pending: 'var(--warning)', rejected: 'var(--error)' }

export default function OutputPanel({ selectedCourse, courses, onCoursesChanged, active, isProcessing, onProcessingChange }: OutputPanelProps) {
  const [knowledgePoints, setKnowledgePoints] = useState<KnowledgePoint[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [editId, setEditId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('all')
  const [showAiPlan, setShowAiPlan] = useState(false)
  const [showZipPlan, setShowZipPlan] = useState(false)
  const [zipCourse, setZipCourse] = useState('')
  const [includeSources, setIncludeSources] = useState(false)
  const [zipBusy, setZipBusy] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const [reloadVersion, setReloadVersion] = useState(0)

  const [manual, setManual] = useState<HumanReviewDetail | null>(null)
  const [manualId, setManualId] = useState('')
  const [manualBusy, setManualBusy] = useState(false)
  const manualRequest = useRef(0)
  const [aiLimit, setAiLimit] = useState(5)
  const [aiBudget, setAiBudget] = useState('0.10')
  const courseTitle = selectedCourse === '通用规范' ? '规范条文库' : courses.find(course => course.name === selectedCourse)?.name || selectedCourse || '全部课程'

  useEffect(() => {
    setSelectedIds([]); setEditId('')
    manualRequest.current += 1
    setManual(null); setManualId(''); setManualBusy(false)
    if (!active || isProcessing) return
    let cancelled = false
    setLoading(true)
    setError('')
    setKnowledgePoints([])
    setPage(1)
    window.electronAPI.getKnowledgePoints(selectedCourse ? { course: selectedCourse } : undefined).then(kps => {
      if (!cancelled) setKnowledgePoints(kps)
    }).catch(err => { if (!cancelled) setError(`知识点读取失败：${String(err)}`) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true; manualRequest.current += 1 }
  }, [selectedCourse, active, isProcessing, reloadVersion])

  useEffect(() => { setSelectedIds([]) }, [statusFilter])
  const reloadPoints = async () => {
    const request = manualRequest.current
    const points = await window.electronAPI.getKnowledgePoints(selectedCourse ? { course: selectedCourse } : undefined)
    if (request === manualRequest.current) { setKnowledgePoints(points); setManual(null); setManualId('') }
  }

  const openManualReview = async (kp: KnowledgePoint) => {
    if (manualBusy || isProcessing) return
    const request = ++manualRequest.current
    setManualId(kp.kp_id); setManual(null); setManualBusy(true); setNotice('')
    try {
      const detail = await window.electronAPI.inspectHumanReview(kp.kp_id)
      if (request === manualRequest.current) setManual(detail)
    } catch (err) { if (request === manualRequest.current) { setNotice(`读取核验依据失败：${String(err)}`); setManualId('') } }
    finally { if (request === manualRequest.current) setManualBusy(false) }
  }
  const confirmManualReview = async () => {
    if (!manual || manualBusy || isProcessing) return
    const request = ++manualRequest.current
    setManualBusy(true)
    try {
      const result = await window.electronAPI.approveHumanReview(manual.kp_id, manual.token)
      if (request !== manualRequest.current) return
      setKnowledgePoints(points => points.map(kp => kp.kp_id === result.kp_id ? { ...kp, ...result } : kp))
      setManual(null); setManualId('')
      setNotice('已保存为“人工核验 · 已验证”。可继续核验下一条，完成后点击“生成复习产物”更新背诵材料。')
    } catch (err) { if (request === manualRequest.current) setNotice(`保存失败：${String(err)}`) }
    finally { if (request === manualRequest.current) setManualBusy(false) }
  }

  const handleAiReview = async () => {
    if (isProcessing || manualBusy) return
    onProcessingChange(true)
    setNotice('正在联网核验待确认条目，进度可在处理流程日志查看…')
    try {
      const result = await window.electronAPI.runCommand('ai-review', ['--limit', String(aiLimit), '--budget-usd', aiBudget, ...(selectedCourse ? ['--course', selectedCourse] : [])])
      setNotice(result.success ? 'AI 自查完成，已刷新核验状态与背诵材料。未通过的条目仍在待确认清单。' : `AI 自查已停止并保存完成结果：${(result.stderr || result.stdout).slice(-450)}`)
    } catch (err) { setNotice(`AI 自查失败：${String(err)}`) }
    finally { onProcessingChange(false) }
  }

  const handleImportZip = async () => {
    if (zipBusy || importBusy || isProcessing || manualBusy) return
    setImportBusy(true)
    setNotice('')
    try {
      const result = await window.electronAPI.importKnowledgeZip()
      if (!result) return
      const conflicts = result.source_conflicts ? `。保留 ${result.source_conflicts} 份本地源文件或原文定位记录` : ''
      const pending = result.pending_count ? `，${result.pending_count} 条已转为待确认` : ''
      setNotice(`已导入 ${result.added} 条，跳过 ${result.skipped} 条已有编号，恢复 ${result.source_count} 份源文件${pending}${conflicts}。`)
      if (result.added) {
        setReloadVersion(version => version + 1)
        await onCoursesChanged()
      }
    } catch (err) {
      setNotice(`导入失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setImportBusy(false)
    }
  }

  const handleExportZip = async () => {
    if (zipBusy || importBusy || isProcessing || manualBusy) return
    setZipBusy(true)
    setNotice('')
    try {
      const result = await window.electronAPI.exportKnowledgeZip(zipCourse, includeSources)
      if (!result) return
      const sources = includeSources ? `，${result.source_count} 份引用源文件` : ''
      const missing = result.missing_count ? `。${result.missing_count} 份源文件待补齐，详见包内目录` : ''
      setNotice(`已导出 ${result.course_count} 个科目、${result.point_count} 条知识点${sources}${missing}。保存位置 ${result.path}`)
      setShowZipPlan(false)
    } catch (err) {
      setNotice(`导出失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setZipBusy(false)
    }
  }

  const handleExportText = async () => {
    if (!knowledgePoints.length || loading) return
    try {
      const savePath = await window.electronAPI.saveFile(`知识点导出_${courseTitle}_${new Date().toLocaleDateString('sv-SE')}.md`)
      if (!savePath) return
      let md = `# ${courseTitle} 知识点\n\n生成时间：${new Date().toLocaleString()}\n\n共 ${knowledgePoints.length} 条知识点\n\n`
      knowledgePoints.forEach((kp, index) => {
        const pages = kp.page_label_start === kp.page_label_end ? kp.page_label_start : `${kp.page_label_start}–${kp.page_label_end}`
        md += `## ${index + 1}. ${kp.title}\n\n${kp.content}\n\n**来源：** ${kp.source_file}（${pages}）\n\n**重要度：** ${kp.importance} | **状态：** ${statusLabels[kp.status] || kp.status}\n\n`
        if (kp.verification_method === 'ai_web' && kp.status === 'verified' && kp.ai_verification) {
          md += `**AI 联网核验：** ${(kp.ai_verification.score * 100).toFixed(1)}%（证据评分）\n\n`
          for (const source of kp.ai_verification.sources || []) md += `- [${source.title}](${source.url})\n`
        }
        if (kp.verification_method === 'human' && kp.status === 'verified' && kp.human_review) md += `**人工核验：** ${kp.human_review.at} · ${kp.human_review.by}\n\n`
        md += '\n---\n\n'
      })
      const result = await window.electronAPI.writeTextFile(savePath, md)
      setNotice(result.success ? `已导出到：${savePath}` : result.message)
    } catch (err) { setNotice(`导出失败：${String(err)}`) }
  }

  const handleExportReview = async () => {
    if (isProcessing || manualBusy) return
    onProcessingChange(true)
    setNotice('正在生成复习产物…')
    try {
      const result = await window.electronAPI.runCommand('review', selectedCourse ? ['--course', selectedCourse] : [])
      setNotice(result.success ? '复习产物已生成，可点击“打开产物目录”查看' : `生成失败：${result.stderr || `退出码 ${result.exitCode}`}`)
    } catch (err) { setNotice(`生成失败：${String(err)}`) }
    finally { onProcessingChange(false) }
  }
  const handleCourseBook = async () => {
    if (isProcessing || manualBusy || !selectedCourse || selectedCourse === '通用规范') return
    onProcessingChange(true)
    setNotice('正在导出课程阅读版与 PDF…')
    try {
      const review = await window.electronAPI.runCommand('review', ['--course', selectedCourse])
      if (!review.success) throw new Error(review.stderr || `复习产物生成失败，退出码 ${review.exitCode}`)
      const result = await window.electronAPI.runCommand('export', ['--course', selectedCourse, '--pdf'])
      setNotice(result.success ? '已导出 Markdown、可搜索 HTML 和 PDF，可打开产物目录查看' : `导出失败：${result.stderr || `退出码 ${result.exitCode}`}`)
    } catch (err) { setNotice(`导出失败：${String(err)}`) }
    finally { onProcessingChange(false) }
  }
  const aiVerified = (kp: KnowledgePoint) => kp.status === 'verified' && kp.verification_method === 'ai_web'
  const humanVerified = (kp: KnowledgePoint) => kp.status === 'verified' && kp.verification_method === 'human'
  const filteredPoints = knowledgePoints.filter(kp => statusFilter === 'all' || (statusFilter === 'ai' ? aiVerified(kp) : statusFilter === 'human' ? humanVerified(kp) : kp.status === statusFilter))
  const totalPages = Math.max(1, Math.ceil(filteredPoints.length / PAGE_SIZE))
  useEffect(() => { setPage(value => Math.min(value, totalPages)) }, [totalPages])
  const visiblePoints = filteredPoints.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  return (
    <div className="output-panel">
      <div className="controls-section">
        <div><h3 className="results-heading">{courseTitle}</h3><p className="results-hint">AI 自查 ≥80% 且关键检查通过后纳入背诵 · 75%–80% 接近通过，待复核</p></div>
        <div className="export-buttons">
          <button className="btn btn-primary" aria-expanded={showZipPlan} disabled={zipBusy} onClick={() => {
            if (!showZipPlan) setZipCourse(selectedCourse)
            setShowZipPlan(!showZipPlan)
          }}><Icon name="file" size={16} />导出 ZIP</button>
          <button className="btn btn-secondary" disabled={zipBusy || importBusy || isProcessing || manualBusy} onClick={() => void handleImportZip()}>{importBusy ? '正在导入…' : '导入 ZIP'}</button>
          <button className="btn btn-secondary" onClick={() => void handleExportText()} disabled={!knowledgePoints.length || loading}><Icon name="file" size={16} />导出文本</button>
          <button className="btn btn-secondary" onClick={() => void handleExportReview()} disabled={isProcessing || manualBusy}>生成复习产物</button>
          <button className="btn btn-secondary" onClick={() => setShowAiPlan(value => !value)} disabled={isProcessing || manualBusy}>AI 联网自查</button>
          <button className="btn btn-secondary" onClick={() => void handleCourseBook()} disabled={isProcessing || manualBusy || !selectedCourse || selectedCourse === '通用规范'}>课程阅读版 / PDF</button>
          <button className="btn btn-secondary" onClick={() => void window.electronAPI.openOutput(selectedCourse).catch(err => setNotice(String(err)))}>打开产物目录</button>
        </div>
      </div>
      {showZipPlan && <section className="knowledge-zip-options" aria-label="ZIP 导出设置">
        <label>导出范围<select aria-label="导出范围" className="review-filter" value={zipCourse} disabled={zipBusy} onChange={e => setZipCourse(e.target.value)}>
          <option value="">全科</option>
          {courses.map(course => <option key={course.id} value={course.name}>{course.name}</option>)}
        </select></label>
        <label><input aria-label="附带引用源文件" type="checkbox" checked={includeSources} disabled={zipBusy} onChange={e => setIncludeSources(e.target.checked)} />附带引用源文件</label>
        <button className="btn btn-primary" disabled={zipBusy || importBusy || isProcessing || manualBusy} onClick={() => void handleExportZip()}>{zipBusy ? '正在导出…' : '保存 ZIP'}</button>
        <button className="btn btn-secondary" disabled={zipBusy} onClick={() => setShowZipPlan(false)}>关闭</button>
      </section>}
      {showAiPlan && <div className="ai-plan"><div><strong>Flash 联网自查</strong><p>范围：{courseTitle} · 当前待确认 {knowledgePoints.filter(kp => kp.status === 'pending').length} 条。仅使用 deepseek-flash，以不同提示做两轮核对；已有结果优先复用。</p></div><label>本批最多新增<select value={aiLimit} disabled={isProcessing || manualBusy} onChange={e => setAiLimit(Number(e.target.value))}><option value={1}>1 条</option><option value={5}>5 条</option><option value={10}>10 条</option><option value={20}>20 条</option></select></label><label>预算上限（美元）<input type="number" min="0.01" step="0.01" value={aiBudget} disabled={isProcessing || manualBusy} onChange={e => setAiBudget(e.target.value)} /></label><button className="btn btn-primary" disabled={isProcessing || manualBusy || !Number.isFinite(Number(aiBudget)) || Number(aiBudget) <= 0} onClick={() => void handleAiReview()}>开始 Flash 自查</button><p>按官方峰时价保守估算，包含搜索、抓取及模型调用；预算不足自动停止并保存结果。实际费用以服务商账单为准。</p></div>}
      {notice && <p className="results-hint" role="status">{notice}</p>}
      <div className="results-section" aria-busy={loading}>
        {error ? <div className="empty-state" role="alert"><p className="inline-error">{error}</p></div> : loading ? (
          <div className="loading-state" role="status"><div className="spinner" /><p>正在读取知识点…</p></div>
        ) : knowledgePoints.length === 0 ? (
          <div className="empty-state"><div className="empty-icon"><Icon name="book" size={28} /></div><p className="empty-title">这门课程还没有知识点</p><p className="empty-subtitle">可切换课程，或在处理流程中完成抽取。</p></div>
        ) : (
          <div className="knowledge-list">
            <div className="list-summary">共 {knowledgePoints.length.toLocaleString()} 条知识点<span className="summary-detail">已验证 {knowledgePoints.filter(kp => kp.status === 'verified').length.toLocaleString()}（人工 {knowledgePoints.filter(humanVerified).length} · AI {knowledgePoints.filter(aiVerified).length}） · 待确认 {knowledgePoints.filter(kp => kp.status === 'pending').length.toLocaleString()}</span><select className="review-filter" aria-label="核验状态筛选" value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }}><option value="all">全部条目</option><option value="pending">待人工处理</option><option value="ai">AI 已核验</option><option value="human">人工核验</option><option value="verified">全部已核验</option><option value="rejected">已驳回</option></select></div>
            <KnowledgeActions key={selectedCourse} points={filteredPoints} visible={visiblePoints} selected={selectedIds} onSelect={setSelectedIds} editId={editId} onClose={() => setEditId('')} disabled={isProcessing || manualBusy} onBusy={setManualBusy} onChanged={reloadPoints} onNotice={setNotice} />
            <div className="knowledge-items">
              {visiblePoints.map((kp, index) => (
                <article key={kp.kp_id} className="knowledge-card">
                  <div className="card-header"><input type="checkbox" aria-label={`选择知识点：${kp.title}`} checked={selectedIds.includes(kp.kp_id)} disabled={isProcessing || manualBusy} onChange={e => setSelectedIds(ids => e.target.checked ? [...ids, kp.kp_id] : ids.filter(id => id !== kp.kp_id))} /><button className="btn btn-secondary" aria-label={`编辑知识点：${kp.title}`} disabled={isProcessing || manualBusy} onClick={() => { setManual(null); setManualId(''); setEditId(kp.kp_id) }}>编辑</button><span className="card-index">{String((page - 1) * PAGE_SIZE + index + 1).padStart(2, '0')}</span><h4 className="card-title">{kp.title}</h4><span className="card-status" style={{ color: statusColors[kp.status] }}>{humanVerified(kp) ? '人工核验 · 已验证' : aiVerified(kp) && kp.ai_verification ? `AI 已核验 · ${(kp.ai_verification.score * 100).toFixed(1)}%` : statusLabels[kp.status] || kp.status}</span></div>
                  <p className="card-content"><MathText text={kp.content} /></p>
                  {kp.note && <p className="card-content">批注：<MathText text={kp.note} /></p>}
                  {kp.ai_review_result && <details className="ai-review-detail"><summary>{humanVerified(kp) ? '查看历史 AI 自查记录' : aiVerified(kp) ? '查看联网核验依据' : '查看 AI 自查结果与待处理原因'}</summary><p>{kp.ai_review_result.error ? 'API 未完成 · 未评分' : `证据评分 ${(kp.ai_review_result.score * 100).toFixed(1)}%${kp.ai_review_result.score >= 0.75 && kp.ai_review_result.score < 0.8 ? ' · 接近通过，待复核' : ''}`} · {kp.ai_review_result.checked_at?.replace('T', ' ')}{kp.ai_review_result.scope === 'historical_standard' ? ' · 旧版规范口径' : ''}</p><p>{kp.ai_review_result.reason || kp.ai_review_result.error}</p>{(kp.ai_review_result.sources?.length ? kp.ai_review_result.sources : kp.ai_review_result.retrieved_sources || []).map(source => <button key={source.url} className="ai-source-link" onClick={() => void window.electronAPI.openWebSource(source.url).catch(err => setNotice(String(err)))}>{source.title || source.url}</button>)}<p>{kp.ai_review_result.policy === 'ai-web-v2' ? '新版评分：证据覆盖45%、来源质量20%、独立性5%、适用条件20%、两轮一致性10%；80%且关键检查通过后纳入背诵，教材可补充联网证据。' : '历史评分：证据覆盖35%、来源权威25%、独立性15%、适用条件15%、两轮一致性10%，按原90%门槛核验。'}评分不是统计正确概率。两轮提示核对不等同于两种独立模型。核验有效期30天，内容变化后须重验。</p></details>}
                  {humanVerified(kp) && kp.human_review && <p className="results-hint">人工核验时间：{kp.human_review.at.replace('T', ' ')} · {kp.human_review.by}</p>}
                  {kp.status === 'pending' && <div className="human-review-actions"><button className="btn btn-secondary" disabled={manualBusy || isProcessing} onClick={() => void openManualReview(kp)}>人工核验通过</button></div>}
                  {manualId === kp.kp_id && <section className="human-review-panel" aria-label="人工核验依据" aria-busy={manualBusy}>
                    {!manual ? <p>正在读取本地核验依据…</p> : <>
                      <h5>核对本条知识点</h5>
                      <p><MathText text={manual.definition} /></p>
                      {manual.numbers.length > 0 && <p>数值：{manual.numbers.join('、')}</p>}
                      {manual.note && <p>附注：<MathText text={manual.note} /></p>}
                      {manual.issues.length > 0 && <ul>{manual.issues.map((issue, i) => <li key={i}>{issue.detail || issue.type}</li>)}</ul>}
                      <p>来源：{manual.source_file}</p>
                      {manual.evidence.map((e, i) => <blockquote key={i}>{e.page_label}：{e.quote}</blockquote>)}
                      <details><summary>查看本地原文</summary><pre>{manual.source_text || '未找到本地原文'}</pre></details>
                      <p>确认表示你已核对本条定义、数值、公式、附注及引用，相关待处理问题将一并记为人工核验通过。</p>
                      <div className="human-review-actions"><button className="btn btn-primary" disabled={manualBusy || isProcessing || manual.status !== 'pending' || !manual.source_text || !manual.evidence.length} onClick={() => void confirmManualReview()}>{manualBusy ? '正在保存…' : '确认人工核验通过'}</button><button className="btn btn-secondary" disabled={manualBusy} onClick={() => { setManual(null); setManualId('') }}>取消</button></div>
                    </>}
                  </section>}
                  <div className="card-footer"><span className="card-source" title={kp.source_file}><Icon name="file" size={13} /><span>{kp.source_type === 'network' ? '网络 · 对话收录' : kp.source_file.split(/[\\/]/).pop()} · {kp.page_label_start}{kp.page_label_end !== kp.page_label_start ? `–${kp.page_label_end}` : ''}</span></span><span className="card-importance">重要度 {kp.importance} / 5</span></div>
                </article>
              ))}
            </div>
            {filteredPoints.length === 0 && <p className="results-hint">当前筛选下没有条目。</p>}
            <div className="pagination"><span>筛选 {filteredPoints.length} 条 · 第 {page} / {totalPages} 页 · 每页 {PAGE_SIZE} 条</span><div><button className="btn btn-secondary" disabled={page === 1} onClick={() => setPage(prev => prev - 1)}>上一页</button><button className="btn btn-secondary" disabled={page >= totalPages} onClick={() => setPage(prev => prev + 1)}>下一页</button></div></div>
          </div>
        )}
      </div>
    </div>
  )
}
