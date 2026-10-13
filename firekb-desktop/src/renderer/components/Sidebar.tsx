import { useEffect, useState } from 'react'
import './Sidebar.css'
import Icon from './Icon'
import type { IconName } from './Icon'
import type { View } from '../App'
import type { Course } from '../../types/electron'
import AppearanceControls from './AppearanceControls'
import type { AppearanceControlsProps } from './AppearanceControls'
interface SidebarProps {
  currentView: View
  onNavigate: (view: View) => void
  courses: Course[]
  courseError: string
  selectedCourse: string
  onSelectCourse: (course: string) => void
  isProcessing: boolean
  appearance: AppearanceControlsProps
}
const menuItems: { id: View; icon: IconName; label: string }[] = [
  { id: 'input', icon: 'folder', label: '素材输入' },
  { id: 'process', icon: 'process', label: '处理控制' },
  { id: 'output', icon: 'book', label: '结果输出' },
  { id: 'models', icon: 'settings', label: '模型配置' }
]
export default function Sidebar({ currentView, onNavigate, courses, courseError, selectedCourse, onSelectCourse, isProcessing, appearance }: SidebarProps) {
  const [expanded, setExpanded] = useState(false)
  useEffect(() => { if (currentView === 'output') setExpanded(true) }, [currentView])
  return (
    <aside className="sidebar">
      <div className="sidebar-header"><div className="brand-mark"><Icon name="flame" size={18} /></div><h1 className="app-title">FireKB<span className="brand-dot">.</span></h1></div>
      <div className="sidebar-scroll">
        <nav className="sidebar-nav" aria-label="工作空间">
          <div className="nav-glass" aria-hidden="true" style={{ transform: `translateY(${menuItems.findIndex(item => item.id === currentView) * 50}px)` }} />
          {menuItems.map(item => <button key={item.id} className={`nav-item ${currentView === item.id ? 'active' : ''}`} aria-current={currentView === item.id ? 'page' : undefined} onClick={() => onNavigate(item.id)}><Icon name={item.icon} size={18} /><span className="nav-label">{item.label}</span></button>)}
        </nav>
        <div className={`course-navigation ${expanded ? 'expanded' : ''}`}>
          <button className="course-toggle" aria-expanded={expanded} aria-controls="sidebar-courses" onClick={() => setExpanded(prev => !prev)}><span>课程与规范</span><span className="course-toggle-meta">{courses.filter(course => course.category !== 'reference').length}<Icon name="arrow" size={11} /></span></button>
          <nav id="sidebar-courses" className="course-list" aria-label="课程分类" hidden={!expanded}>
            <button className={`course-item ${currentView === 'output' && !selectedCourse ? 'selected' : ''}`} aria-pressed={currentView === 'output' && !selectedCourse} onClick={() => onSelectCourse('')}><span className="course-dot all" /><span>全部课程</span></button>
            {courses.map(course => <button key={course.id} className={`course-item ${currentView === 'output' && selectedCourse === course.name ? 'selected' : ''}`} aria-pressed={currentView === 'output' && selectedCourse === course.name} onClick={() => onSelectCourse(course.name)} title={course.name}><span className="course-dot" /><span>{course.name === '通用规范' ? '规范条文库' : course.name}</span></button>)}
          </nav>
          {courseError && <p className="sidebar-error" role="alert">{courseError}</p>}
        </div>
      </div>
      <div className="sidebar-footer"><div className="version"><i className={`status-dot ${isProcessing ? 'busy' : ''}`} /><span>{isProcessing ? '处理中' : '本地空间'}</span></div><AppearanceControls {...appearance} /></div>
    </aside>
  )
}
