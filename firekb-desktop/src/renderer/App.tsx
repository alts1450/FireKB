import { useState, useEffect } from 'react'
import type { PointerEvent } from 'react'
import type { Course } from '../types/electron'
import './App.css'
import Sidebar from './components/Sidebar'
import Header from './components/Header'
import MaterialInput from './components/MaterialInput'
import ProcessControl from './components/ProcessControl'
import OutputPanel from './components/OutputPanel'
import ModelConfig from './components/ModelConfig'
import useAppearance from './useAppearance'

export type View = 'input' | 'process' | 'output' | 'models'
const VIEW_META: Record<View, { title: string; subtitle: string; label: string }> = {
  input: { title: '从素材开始，构建你的知识库。', subtitle: '整理课件与教材，让每一个知识点都有据可查。', label: '素材输入' },
  process: { title: '让知识，逐步清晰。', subtitle: '从页级文本到引用核验，按顺序完成知识整理。', label: '处理控制' },
  output: { title: '你的知识，随时可查。', subtitle: '按课程浏览知识点，保留原始文件与页码出处。', label: '结果输出' },
  models: { title: '为每一步，选择合适的引擎。', subtitle: '管理解析、识别与转写，以及知识抽取使用的模型。', label: '模型配置' }
}
function App() {
  const appearance = useAppearance()
  const [currentView, setCurrentView] = useState<View>('input')
  const [visited, setVisited] = useState<View[]>(['input'])
  const [materials, setMaterials] = useState<string[]>([])
  const [isProcessing, setIsProcessing] = useState(false)
  const [courses, setCourses] = useState<Course[]>([])
  const [courseError, setCourseError] = useState('')
  const [selectedCourse, setSelectedCourse] = useState('')
  useEffect(() => {
    let active = true
    window.electronAPI.getCourses().then(data => {
      if (active) setCourses(data)
    }).catch(error => { if (active) setCourseError(`课程读取失败：${String(error)}`) })
    return () => { active = false }
  }, [])
  const navigate = (view: View) => {
    setVisited(prev => prev.includes(view) ? prev : [...prev, view])
    setCurrentView(view)
  }
  // Keep the effect local to a button; pointer animation does not rerender React.
  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const button = (event.target as Element).closest('button')
    if (!button || button.disabled) return
    const rect = button.getBoundingClientRect()
    const ripple = document.createElement('span')
    ripple.className = 'glass-ripple'
    ripple.style.left = `${event.clientX - rect.left}px`
    ripple.style.top = `${event.clientY - rect.top}px`
    button.append(ripple)
    const animation = ripple.animate([
      { transform: 'translate(-50%, -50%) scale(0)', opacity: 0.3 },
      { transform: 'translate(-50%, -50%) scale(1)', opacity: 0 }
    ], { duration: 320, easing: 'cubic-bezier(.2,.7,.2,1)' })
    void animation.finished.then(() => ripple.remove(), () => ripple.remove())
  }
  return (
    <div className="app" onPointerDown={handlePointerDown}>
      <Sidebar currentView={currentView} onNavigate={navigate} courses={courses} courseError={courseError}
        selectedCourse={selectedCourse} onSelectCourse={course => { setSelectedCourse(course); navigate('output') }} isProcessing={isProcessing}
        appearance={{ mode: appearance.mode, accent: appearance.accent, theme: appearance.theme, onModeChange: appearance.setMode, onAccentChange: appearance.setAccent }} />
      <main className="main-content">
        <Header {...VIEW_META[currentView]} isProcessing={isProcessing} compact={currentView === 'input'} />
        <div className={`content-area ${currentView === 'input' ? 'content-area-input' : ''}`}>
          {visited.map(view => (
            <section key={view} className="view-panel" hidden={view !== currentView} aria-label={VIEW_META[view].label}>
              {view === 'input' && <MaterialInput materials={materials}
                onAdd={paths => setMaterials(prev => [...new Set([...prev, ...paths.filter(Boolean)])])}
                onRemove={path => setMaterials(prev => prev.filter(item => item !== path))}
                onClear={() => setMaterials([])} onContinue={() => navigate('process')} />}
              {view === 'process' && <ProcessControl materials={materials} isProcessing={isProcessing} onProcessingChange={setIsProcessing} />}
              {view === 'output' && <OutputPanel selectedCourse={selectedCourse} courses={courses} active={currentView === 'output'} isProcessing={isProcessing} onProcessingChange={setIsProcessing} />}
              {view === 'models' && <ModelConfig />}
            </section>
          ))}
        </div>
      </main>
    </div>
  )
}
export default App
