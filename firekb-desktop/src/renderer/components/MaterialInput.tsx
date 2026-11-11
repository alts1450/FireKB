import { useState } from 'react'
import Icon from './Icon'
import './MaterialInput.css'
import MaterialIntake from './MaterialIntake'
interface MaterialInputProps {
  materials: string[]
  onAdd: (paths: string[]) => void
  onRemove: (path: string) => void
  onClear: () => void
  onContinue: () => void
  onTimetable: () => void
}
export default function MaterialInput({ materials, onAdd, onRemove, onClear, onContinue, onTimetable }: MaterialInputProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [error, setError] = useState('')
  const select = async (folder: boolean) => {
    setError('')
    try {
      if (folder) { const path = await window.electronAPI.selectFolder(); if (path) onAdd([path]) }
      else { const paths = await window.electronAPI.selectFiles(); onAdd(paths) }
    } catch (err) { setError(`无法选择素材：${String(err)}`) }
  }
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    const paths = Array.from(e.dataTransfer.files).map(file => window.electronAPI.getPathForFile(file)).filter(Boolean)
    if (paths.length) onAdd(paths)
  }
  const fileName = (path: string) => path.split(/[\\/]/).pop() || path
  const fileType = (path: string) => {
    const ext = path.split('.').pop()?.toUpperCase()
    return ext && ext.length <= 5 ? ext : '文件夹'
  }
  return (
    <div className="material-input">
      <div className={`material-hero ${isDragging ? 'dragging' : ''}`}
        onDragOver={e => { e.preventDefault(); setIsDragging(true) }}
        onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragging(false) }} onDrop={handleDrop}>
        <div className="hero-brand"><div className="hero-mark"><Icon name="flame" size={34} /></div><h2>FireKB<span>.</span></h2><p>消防工程知识库</p></div>
        <h3 className="hero-description">从素材开始，构建你的知识库。</h3>
        <div className="material-tray"><Icon name="file" size={18} /><span>{isDragging ? '松开鼠标，添加学习素材' : '拖入学习素材，或从本地选择文件…'}</span><button className="tray-select" aria-label="选择学习素材" onClick={() => void select(false)}><Icon name="arrow" size={18} /></button></div>
        <p className="hero-hint">支持拖入文件或文件夹，也可以从本地选择</p>
        <div className="button-group"><button className="btn btn-secondary" onClick={() => void select(false)}><Icon name="file" size={15} />选择文件</button><button className="btn btn-secondary" onClick={() => void select(true)}><Icon name="folder" size={15} />选择文件夹</button></div>
        <div className="format-tags"><span className="format-note">支持</span><span>PDF</span><span>PPTX</span><span>EPUB</span><span>AAC / M4A</span><span className="format-note">课件 / 教材 / 录音</span></div>
        {error && <p className="inline-error" role="alert">{error}</p>}
      </div>
      {materials.length > 0 && <div className="materials-list">
        <div className="list-header"><h3 className="list-title">素材清单 <span className="count-pill">{materials.length}</span></h3><button className="btn-text" onClick={onClear}>清空全部</button></div>
        <div className="list-items">{materials.map(path => <div key={path} className="material-item"><span className="file-type">{fileType(path)}</span><div className="item-content"><p className="item-name">{fileName(path)}</p><p className="item-path" title={path}>{path}</p></div><button className="btn-icon" onClick={() => onRemove(path)} aria-label={`移除 ${fileName(path)}`}><Icon name="close" size={15} /></button></div>)}</div>
      </div>}
      {materials.length > 0 && <MaterialIntake paths={materials} onTimetable={onTimetable} />}
      <div className="material-next"><button className="btn-text" onClick={onTimetable}>课表设置与导入</button><button className="btn-text" onClick={onContinue}>进入处理流程<Icon name="arrow" size={13} /></button></div>
    </div>
  )
}
