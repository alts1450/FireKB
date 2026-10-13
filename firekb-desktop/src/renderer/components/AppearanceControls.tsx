import { useEffect, useRef, useState } from 'react'
import Icon from './Icon'
import { ACCENTS } from '../useAppearance'
import type { AccentColor, AppearanceMode } from '../useAppearance'
import './AppearanceControls.css'

export interface AppearanceControlsProps {
  mode: AppearanceMode
  accent: AccentColor
  theme: 'light' | 'dark'
  onModeChange: (mode: AppearanceMode) => void
  onAccentChange: (accent: AccentColor) => void
}
export default function AppearanceControls({ mode, accent, theme, onModeChange, onAccentChange }: AppearanceControlsProps) {
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const closeOutside = (event: PointerEvent) => {
      if (!panelRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); triggerRef.current?.focus() }
    }
    document.addEventListener('pointerdown', closeOutside)
    document.addEventListener('keydown', closeWithEscape)
    return () => { document.removeEventListener('pointerdown', closeOutside); document.removeEventListener('keydown', closeWithEscape) }
  }, [open])
  return (
    <div className="appearance" ref={panelRef}>
      <button ref={triggerRef} className={`appearance-trigger ${open ? 'active' : ''}`} aria-expanded={open} aria-controls="appearance-panel" onClick={() => setOpen(prev => !prev)}>
        <Icon name={theme === 'dark' ? 'moon' : 'sun'} size={15} /><span>外观</span>
      </button>
      {open && <div id="appearance-panel" className="appearance-panel" role="region" aria-label="外观设置">
        <h3>外观</h3><p>让工作空间，适合你的习惯。</p>
        <fieldset className="appearance-field"><legend>显示模式</legend><div className="mode-options">
          {([{ id: 'light', label: '浅色' }, { id: 'dark', label: '深色' }, { id: 'system', label: '跟随系统' }] as const).map(item => <label key={item.id}>
            <input type="radio" name="appearance-mode" value={item.id} checked={mode === item.id} onChange={() => onModeChange(item.id)} /><span>{item.label}</span>
          </label>)}
        </div></fieldset>
        <fieldset className="appearance-field"><legend>强调色</legend><div className="accent-options">
          {ACCENTS.map(item => <label key={item.id} title={item.label}>
            <input type="radio" name="appearance-accent" value={item.id} checked={accent === item.id} onChange={() => onAccentChange(item.id)} />
            <span className="accent-swatch" style={{ '--swatch': item.color } as React.CSSProperties} /><span>{item.label}</span>
          </label>)}
        </div></fieldset>
        <div className="appearance-caption">外观偏好会自动保存</div>
      </div>}
    </div>
  )
}
