import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { AssistantRuntimeProvider, useLocalRuntime, ThreadPrimitive, MessagePrimitive, ComposerPrimitive, type ChatModelAdapter } from '@assistant-ui/react'
import type { ChatAnswer, ChatOptions, Course } from '../../types/electron'
import MathText from './MathText'
import { selectedAnswerText, type AnswerRange, type AnswerSelection } from './chatSelection'
import './KnowledgeChat.css'

const CollectionContext = createContext<{ courses: Course[]; course: string }>({ courses: [], course: '' })
function CollectAnswer({ answer, selectionDraft }: { answer: ChatAnswer; selectionDraft: AnswerSelection | null }) {
  const { courses, course } = useContext(CollectionContext)
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<number[]>([])
  const [title, setTitle] = useState(''); const [note, setNote] = useState(''); const [definition, setDefinition] = useState('')
  const [courseId, setCourseId] = useState(courses.find(c => c.name === course)?.id || '')
  const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('')
  const [ranges, setRanges] = useState<AnswerRange[]>([])
  const editor = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!selectionDraft) return
    setOpen(true); setRanges(selectionDraft.ranges); setSelected([...new Set(selectionDraft.ranges.map(r => r.index))])
    setDefinition(selectionDraft.text); setTitle(selectionDraft.text.trim().split('\n')[0].slice(0, 50)); setNote(''); setNotice('')
  }, [selectionDraft])
  useEffect(() => { if (open && selectionDraft) editor.current?.scrollIntoView({ block: 'nearest' }) }, [open, selectionDraft])
  const toggle = (index: number) => {
    const next = (selected.includes(index) ? selected.filter(i => i !== index) : [...selected, index]).sort((a, b) => a - b)
    setSelected(next); setDefinition(next.map(i => answer.segments[i].text).join('\n\n'))
  }
  const save = async () => {
    if (busy || !selected.length || !courseId || !title.trim() || !definition.trim()) return
    setBusy(true); setNotice('')
    try {
      const result = await window.electronAPI.chat(crypto.randomUUID(), { action: 'collect', answer_id: answer.answer_id, indices: selected, ...(ranges.length ? { ranges } : {}), course_id: courseId, title, definition, note, confirmed: true })
      setNotice(`${result.duplicate ? '已收录，无需重复保存' : '已收录'}：${result.kp_id} · 人工核验 · 来源：网络`)
    } catch (e) { setNotice(`收录失败：${String(e)}`) }
    finally { setBusy(false) }
  }
  if (!answer.answer_id) return null
  return <div className="chat-collect"><button className="btn btn-secondary" disabled={busy} onClick={() => setOpen(v => !v)}>批注 / 收录知识点</button>
    {open && <div ref={editor} className="chat-collect-editor"><h4>{ranges.length ? '收录选中文字' : '勾选要收录的回答段落'}</h4>
      {ranges.length ? <button className="btn btn-secondary" disabled={busy} onClick={() => { setRanges([]); setSelected([]); setDefinition('') }}>改用整段勾选</button> : <div className="chat-select-segments">{answer.segments.map((part, i) => part.text.trim() && <label key={i}><input aria-label={`收录段落 ${i + 1}`} type="checkbox" checked={selected.includes(i)} disabled={busy} onChange={() => toggle(i)} /><span><MathText text={part.text} /></span></label>)}</div>}
      <label>归属课程<select aria-label="收录课程" value={courseId} disabled={busy} onChange={e => setCourseId(e.target.value)}><option value="">请选择课程</option>{courses.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>知识点标题<input aria-label="知识点标题" maxLength={150} value={title} disabled={busy} onChange={e => setTitle(e.target.value)} /></label>
      <label>知识点正文<textarea aria-label="知识点正文" rows={5} maxLength={30000} value={definition} disabled={busy} onChange={e => setDefinition(e.target.value)} /></label>
      <label>批注<textarea aria-label="知识点批注" rows={2} maxLength={5000} value={note} disabled={busy} onChange={e => setNote(e.target.value)} /></label>
      <p className="chat-meta">保存表示已由你核验，将标记为“人工核验、来源：网络”。{!ranges.length && '重新勾选段落会重新填充正文。'}</p>
      <button className="btn btn-primary" disabled={busy || !selected.length || !courseId || !title.trim() || !definition.trim()} onClick={() => void save()}>{busy ? '正在收录…' : '人工核验并收录'}</button>
    </div>}{notice && <p role="status" className="chat-meta">{notice}</p>}
  </div>
}

function AnswerText({ text }: { text: string }) {
  const body = useRef<HTMLDivElement>(null)
  const [selection, setSelection] = useState<AnswerSelection | null>(null)
  const [selectionDraft, setSelectionDraft] = useState<AnswerSelection | null>(null)
  useEffect(() => {
    const clear = () => {
      const current = window.getSelection()
      if (!current || current.isCollapsed || !body.current?.contains(current.anchorNode) || !body.current?.contains(current.focusNode)) setSelection(null)
    }
    document.addEventListener('selectionchange', clear)
    return () => document.removeEventListener('selectionchange', clear)
  }, [])
  let answer: ChatAnswer
  try { answer = JSON.parse(text) } catch { return <span className="chat-unsupported">{text}</span> }
  const capture = () => { if (body.current && answer.answer_id) setSelection(selectedAnswerText(body.current, answer.segments.map(p => p.text))) }
  return <div><div ref={body} className="chat-answer-body" onMouseUp={capture} onKeyUp={capture}>{answer.segments.map((part, i) => <span key={i} data-chat-segment={i} className={`chat-${part.citation ? 'cited' : part.kind}`} tabIndex={part.citation ? 0 : undefined}>
    {part.text.split(/(\$[^$]+\$)/g).map((chunk, j, chunks) => { const start = chunks.slice(0, j).join('').length; return <span key={j} data-chat-start={start} data-chat-end={start + chunk.length} data-chat-math={chunk.startsWith('$')}><MathText text={chunk} /></span> })}
    {part.citation && <span className="chat-citation" role="tooltip"><strong>{part.citation.name} · {part.citation.id}</strong><span>{part.citation.source} · {part.citation.page}</span><span>{part.citation.original}</span>{part.citation.coverage_note && <span>局部抽取 · 原文已定位</span>}</span>}
  </span>)}</div>{selection && <button className="btn btn-primary chat-selection-action" style={{ left: selection.left, top: selection.top }} onMouseDown={e => e.preventDefault()} onClick={() => { setSelectionDraft(selection); setSelection(null); window.getSelection()?.removeAllRanges() }}>收录选中文字</button>}<p className="chat-meta">{answer.model} · 检索 {answer.source_count} 条 · 回答采用 {answer.cited_source_count ?? new Set(answer.segments.flatMap(p => p.citation ? [p.citation.id] : [])).size} 条</p>{answer.web_sources?.map(s => <button key={s.url} className="ai-source-link" onClick={() => void window.electronAPI.openWebSource(s.url)}>{s.title || s.url}</button>)}{answer.web_error && <p className="chat-meta">联网补充暂不可用，本次使用知识库与模型自身知识回答。</p>}{answer.warning && <p role="status" className="chat-meta">{answer.warning}</p>}<CollectAnswer answer={answer} selectionDraft={selectionDraft} /></div>
}
function AssistantMessage() { return <MessagePrimitive.Root className="chat-message assistant"><strong>FireKB</strong><MessagePrimitive.Parts components={{ Text: AnswerText }} /></MessagePrimitive.Root> }
function UserMessage() { return <MessagePrimitive.Root className="chat-message user"><strong>你</strong><MessagePrimitive.Parts /></MessagePrimitive.Root> }

function Conversation({ model, effort, course, web }: { model: string; effort: string; course: string; web: boolean }) {
  const [error, setError] = useState('')
  const adapter = useMemo<ChatModelAdapter>(() => ({ async run({ messages, abortSignal }) {
    const id = crypto.randomUUID()
    const cancel = () => { void window.electronAPI.cancelChat(id) }
    abortSignal.addEventListener('abort', cancel, { once: true })
    setError('')
    try {
      if (abortSignal.aborted) throw new Error('已取消')
      const history = messages.map(m => {
        let content = m.content.filter(p => p.type === 'text').map(p => p.text).join('\n')
        if (m.role === 'assistant') { try { content = (JSON.parse(content) as ChatAnswer).segments.map(p => p.text).join('') } catch { /* Plain earlier messages. */ } }
        return { role: m.role, content }
      })
      const result = await window.electronAPI.chat(id, { messages: history, model, effort, course, web })
      if (abortSignal.aborted) throw new Error('已取消')
      return { content: [{ type: 'text', text: JSON.stringify(result) }] }
    } catch (e) { if (!abortSignal.aborted) setError(String(e)); throw e }
    finally { abortSignal.removeEventListener('abort', cancel) }
  } }), [model, effort, course, web])
  const runtime = useLocalRuntime(adapter)
  useEffect(() => () => runtime.thread.cancelRun(), [runtime])
  return <AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Root className="chat-thread">
    <ThreadPrimitive.Viewport className="chat-viewport"><ThreadPrimitive.Empty><div className="chat-empty"><h3>从一个问题开始</h3><p>例如：消防水池的有效容积如何确定？</p><p>可以解释知识点、演算题目，也可以继续追问。</p></div></ThreadPrimitive.Empty><ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} /><ThreadPrimitive.If running><p role="status">正在检索与生成回答…</p></ThreadPrimitive.If></ThreadPrimitive.Viewport>
    {error && <p role="alert" className="inline-error">{error}</p>}
    <ComposerPrimitive.Root className="chat-composer"><ComposerPrimitive.Input aria-label="输入问题" placeholder="询问知识库… Enter 发送，Shift+Enter 换行" rows={3} maxLength={12000} /><div><ComposerPrimitive.Send className="btn btn-primary">发送</ComposerPrimitive.Send><ComposerPrimitive.Cancel className="btn btn-secondary">停止</ComposerPrimitive.Cancel></div></ComposerPrimitive.Root>
  </ThreadPrimitive.Root></AssistantRuntimeProvider>
}

export default function KnowledgeChat({ courses }: { courses: Course[] }) {
  const [options, setOptions] = useState<ChatOptions | null>(null)
  const [model, setModel] = useState(''); const [effort, setEffort] = useState('none'); const [course, setCourse] = useState('')
  const [web, setWeb] = useState(false)
  const [epoch, setEpoch] = useState(0); const [error, setError] = useState('')
  useEffect(() => { window.electronAPI.chat(crypto.randomUUID(), { action: 'options' }).then(o => { setOptions(o); setModel(o.defaultModel) }).catch(e => setError(String(e))) }, [])
  return <CollectionContext.Provider value={{ courses, course }}><div className="knowledge-chat"><div className="chat-controls">
    <label>模型<select value={model} onChange={e => setModel(e.target.value)}>{options?.models.map(m => <option key={m}>{m}</option>)}</select></label>
    <label>推理强度<select value={effort} onChange={e => setEffort(e.target.value)}>{options?.efforts.map(v => <option key={v} value={v}>{{ none: '关闭（省额度）', low: '低', high: '高', max: '最高' }[v] || v}</option>)}</select></label>
    <label>知识范围<select value={course} onChange={e => setCourse(e.target.value)}><option value="">全部课程</option>{courses.map(c => <option key={c.id} value={c.name}>{c.name}</option>)}</select></label>
    <label className="chat-web-option"><input type="checkbox" checked={web} onChange={e => setWeb(e.target.checked)} />联网补充</label>
    <button className="btn btn-secondary" onClick={() => setEpoch(e => e + 1)}>新对话</button>
  </div>
    {error && <p role="alert">{error}</p>}{options && model && <Conversation key={epoch} model={model} effort={effort} course={course} web={web} />}
  </div></CollectionContext.Provider>
}
