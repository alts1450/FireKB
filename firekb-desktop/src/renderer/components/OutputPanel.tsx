import { useState, useEffect } from 'react'
import './OutputPanel.css'
import Icon from './Icon'
import type { Course, KnowledgePoint } from '../../types/electron'

interface OutputPanelProps {
  selectedCourse: string
  courses: Course[]
  active: boolean
  isProcessing: boolean
  onProcessingChange: (processing: boolean) => void
}
const PAGE_SIZE = 40
const statusLabels = { verified: '已验证', pending: '待确认', rejected: '已拒绝' }
const statusColors = { verified: 'var(--success)', pending: 'var(--warning)', rejected: 'var(--error)' }

export default function OutputPanel({ selectedCourse, courses, active, isProcessing, onProcessingChange }: OutputPanelProps) {
  const [knowledgePoints, setKnowledgePoints] = useState<KnowledgePoint[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState(1)
  const courseTitle = selectedCourse === '通用规范' ? '规范条文库' : courses.find(course => course.name === selectedCourse)?.name || selectedCourse || '全部课程'

  useEffect(() => {
    if (!active) return
    let cancelled = false
    setLoading(true)
    setError('')
    setNotice('')
    setKnowledgePoints([])
    setPage(1)
    window.electronAPI.getKnowledgePoints(selectedCourse ? { course: selectedCourse } : undefined).then(kps => {
      if (!cancelled) setKnowledgePoints(kps)
    }).catch(err => { if (!cancelled) setError(`知识点读取失败：${String(err)}`) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [selectedCourse, active])

  const handleExportText = async () => {
    if (!knowledgePoints.length || loading) return
    try {
      const savePath = await window.electronAPI.saveFile(`知识点导出_${courseTitle}_${new Date().toLocaleDateString('sv-SE')}.md`)
      if (!savePath) return
      let md = `# ${courseTitle} 知识点\n\n生成时间：${new Date().toLocaleString()}\n\n共 ${knowledgePoints.length} 条知识点\n\n`
      knowledgePoints.forEach((kp, index) => {
        const pages = kp.page_label_start === kp.page_label_end ? kp.page_label_start : `${kp.page_label_start}–${kp.page_label_end}`
        md += `## ${index + 1}. ${kp.title}\n\n${kp.content}\n\n**来源：** ${kp.source_file}（${pages}）\n\n**重要度：** ${kp.importance} | **状态：** ${statusLabels[kp.status] || kp.status}\n\n---\n\n`
      })
      const result = await window.electronAPI.writeTextFile(savePath, md)
      setNotice(result.success ? `已导出到：${savePath}` : result.message)
    } catch (err) { setNotice(`导出失败：${String(err)}`) }
  }

  const handleExportReview = async () => {
    if (isProcessing) return
    onProcessingChange(true)
    setNotice('正在生成复习产物…')
    try {
      const result = await window.electronAPI.runCommand('review')
      setNotice(result.success ? '复习产物已生成到 40_复习产物/ 目录' : `生成失败：${result.stderr || `退出码 ${result.exitCode}`}`)
    } catch (err) { setNotice(`生成失败：${String(err)}`) }
    finally { onProcessingChange(false) }
  }
  const totalPages = Math.max(1, Math.ceil(knowledgePoints.length / PAGE_SIZE))
  const visiblePoints = knowledgePoints.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  return (
    <div className="output-panel">
      <div className="controls-section">
        <div><h3 className="results-heading">{courseTitle}</h3><p className="results-hint">在左侧切换课程 · 待确认条目请先复核原文</p></div>
        <div className="export-buttons">
          <button className="btn btn-primary" onClick={() => void handleExportText()} disabled={!knowledgePoints.length || loading}><Icon name="file" size={16} />导出文本</button>
          <button className="btn btn-secondary" onClick={() => void handleExportReview()} disabled={isProcessing}>生成复习产物</button>
        </div>
      </div>
      {notice && <p className="results-hint" role="status">{notice}</p>}
      <div className="results-section" aria-busy={loading}>
        {error ? <div className="empty-state" role="alert"><p className="inline-error">{error}</p></div> : loading ? (
          <div className="loading-state" role="status"><div className="spinner" /><p>正在读取知识点…</p></div>
        ) : knowledgePoints.length === 0 ? (
          <div className="empty-state"><div className="empty-icon"><Icon name="book" size={28} /></div><p className="empty-title">这门课程还没有知识点</p><p className="empty-subtitle">可切换课程，或在处理流程中完成抽取。</p></div>
        ) : (
          <div className="knowledge-list">
            <div className="list-summary">共 {knowledgePoints.length.toLocaleString()} 条知识点<span className="summary-detail">已验证 {knowledgePoints.filter(kp => kp.status === 'verified').length.toLocaleString()} · 待确认 {knowledgePoints.filter(kp => kp.status === 'pending').length.toLocaleString()}</span></div>
            <div className="knowledge-items">
              {visiblePoints.map((kp, index) => (
                <article key={kp.kp_id} className="knowledge-card">
                  <div className="card-header"><span className="card-index">{String((page - 1) * PAGE_SIZE + index + 1).padStart(2, '0')}</span><h4 className="card-title">{kp.title}</h4><span className="card-status" style={{ color: statusColors[kp.status] }}>{statusLabels[kp.status] || kp.status}</span></div>
                  <p className="card-content">{kp.content}</p>
                  <div className="card-footer"><span className="card-source" title={kp.source_file}><Icon name="file" size={13} /><span>{kp.source_file.split(/[\\/]/).pop()} · {kp.page_label_start}{kp.page_label_end !== kp.page_label_start ? `–${kp.page_label_end}` : ''}</span></span><span className="card-importance">重要度 {kp.importance} / 5</span></div>
                </article>
              ))}
            </div>
            <div className="pagination"><span>第 {page} / {totalPages} 页 · 每页 {PAGE_SIZE} 条</span><div><button className="btn btn-secondary" disabled={page === 1} onClick={() => setPage(prev => prev - 1)}>上一页</button><button className="btn btn-secondary" disabled={page === totalPages} onClick={() => setPage(prev => prev + 1)}>下一页</button></div></div>
          </div>
        )}
      </div>
    </div>
  )
}
