import { useEffect, useLayoutEffect, useState } from 'react'

export type AppearanceMode = 'light' | 'dark' | 'system'
export type AccentColor = 'blue' | 'green' | 'sand' | 'graphite'
export const ACCENTS: { id: AccentColor; label: string; color: string }[] = [
  { id: 'blue', label: '雾蓝', color: '#376eb4' },
  { id: 'green', label: '苔绿', color: '#477963' },
  { id: 'sand', label: '暖砂', color: '#986b42' },
  { id: 'graphite', label: '石墨', color: '#596575' }
]
const STORAGE_KEY = 'firekb.appearance.v1'
interface Appearance { mode: AppearanceMode; accent: AccentColor }
function readAppearance(): Appearance {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return {
      mode: ['light', 'dark', 'system'].includes(saved.mode) ? saved.mode : 'light',
      accent: ACCENTS.some(item => item.id === saved.accent) ? saved.accent : 'blue'
    }
  } catch { return { mode: 'light', accent: 'blue' } }
}
export default function useAppearance() {
  const [appearance, setAppearance] = useState<Appearance>(readAppearance)
  const [systemDark, setSystemDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches)
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)')
    const update = () => setSystemDark(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  const theme = appearance.mode === 'system' ? (systemDark ? 'dark' : 'light') : appearance.mode
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.dataset.accent = appearance.accent
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(appearance)) } catch { /* Session appearance still works when storage is unavailable. */ }
  }, [appearance, theme])
  useEffect(() => {
    void window.electronAPI.setAppearanceMode(appearance.mode).catch(error => console.error('Window appearance:', error))
  }, [appearance.mode])
  return { ...appearance, theme, setMode: (mode: AppearanceMode) => setAppearance(prev => ({ ...prev, mode })),
    setAccent: (accent: AccentColor) => setAppearance(prev => ({ ...prev, accent })) }
}
