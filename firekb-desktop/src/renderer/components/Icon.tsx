import type { CSSProperties } from 'react'
export type IconName = 'flame' | 'folder' | 'process' | 'book' | 'settings' | 'upload' | 'arrow' | 'file' | 'close' | 'check' | 'sun' | 'moon'
const paths: Record<IconName, string> = {
  flame: 'M12 3c1 4-2 5-2 8 0 2 1 3 2 3 2-1 3-3 3-5 3 3 4 5 4 7a7 7 0 0 1-14 0c0-4 3-6 3-8 0 3 1 4 2 4',
  folder: 'M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z',
  process: 'M5 4v16M12 4v16M19 4v16M2 8h6M9 16h6M16 9h6',
  book: 'M12 5v15M12 5C8 3 5 3 2 4v15c3-1 6-1 10 1 4-2 7-2 10-1V4c-3-1-6-1-10 1Z',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z',
  upload: 'M12 16V3m-5 5 5-5 5 5M4 14v6a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-6',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  file: 'M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h6',
  close: 'm6 6 12 12M6 18 18 6', check: 'm5 12 4 4L19 6',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5',
  moon: 'M20 14a8 8 0 0 1-10-10 8.5 8.5 0 1 0 10 10Z'
}
export default function Icon({ name, size = 20, style }: { name: IconName; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}><path d={paths[name]} /></svg>
}
