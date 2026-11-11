export interface AnswerRange { index: number; start: number; end: number }
export interface AnswerSelection { ranges: AnswerRange[]; text: string; left: number; top: number }

/** Map displayed text to original answer offsets; include a selected formula as LaTeX. */
export function selectedAnswerText(root: HTMLElement, texts: string[]): AnswerSelection | null {
  const selection = window.getSelection()
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null
  const range = selection.getRangeAt(0)
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null
  const ranges: AnswerRange[] = []
  root.querySelectorAll<HTMLElement>('[data-chat-start]').forEach(piece => {
    if (!range.intersectsNode(piece)) return
    const clipped = document.createRange(); clipped.selectNodeContents(piece)
    if (range.compareBoundaryPoints(Range.START_TO_START, clipped) > 0) clipped.setStart(range.startContainer, range.startOffset)
    if (range.compareBoundaryPoints(Range.END_TO_END, clipped) < 0) clipped.setEnd(range.endContainer, range.endOffset)
    if (clipped.collapsed) return
    const index = Number(piece.closest<HTMLElement>('[data-chat-segment]')?.dataset.chatSegment)
    let start = Number(piece.dataset.chatStart); let end = Number(piece.dataset.chatEnd)
    if (piece.dataset.chatMath !== 'true') {
      const prefix = document.createRange(); prefix.selectNodeContents(piece); prefix.setEnd(clipped.startContainer, clipped.startOffset)
      start += prefix.toString().length; end = start + clipped.toString().length
    }
    if (start >= end) return
    const previous = ranges[ranges.length - 1]
    if (previous?.index === index && previous.end >= start) previous.end = Math.max(previous.end, end)
    else ranges.push({ index, start, end })
  })
  const text = ranges.map(r => texts[r.index].slice(r.start, r.end)).join('\n\n')
  if (!text.trim()) return null
  const box = range.getBoundingClientRect()
  return {
    // Python indexes Unicode code points; DOM offsets count UTF-16 units.
    ranges: ranges.map(r => ({ index: r.index, start: [...texts[r.index].slice(0, r.start)].length, end: [...texts[r.index].slice(0, r.end)].length })),
    text, left: Math.max(8, Math.min(box.left, window.innerWidth - 180)), top: Math.max(8, Math.min(box.bottom + 6, window.innerHeight - 48)),
  }
}
