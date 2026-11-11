import { useEffect, useRef, useState } from 'react'
import type { KnowledgePoint, KnowledgeDraft, KnowledgeManagementResult } from '../../types/electron'

interface Props {
  points: KnowledgePoint[]; visible: KnowledgePoint[]; selected: string[]; onSelect: (ids: string[]) => void
  editId: string; onClose: () => void; disabled: boolean; onBusy: (value: boolean) => void
  onChanged: () => Promise<void>; onNotice: (text: string) => void
}
export default function KnowledgeActions(p: Props) {
  const [draft, setDraft] = useState<KnowledgeDraft | null>(null)
  const [token, setToken] = useState(''); const [busy, setBusy] = useState(false)
  const [deletion, setDeletion] = useState<KnowledgeManagementResult | null>(null)
  const [error, setError] = useState('')
  const editor = useRef<HTMLElement>(null)
  useEffect(() => { if (draft) editor.current?.scrollIntoView({ block: 'nearest' }) }, [!!draft, p.editId])
  useEffect(() => {
    setDraft(null); setError('')
    if (!p.editId) return
    let active = true
    window.electronAPI.manageKnowledge({ operation: 'inspect', kp_id: p.editId }).then(r => {
      if (active && r.point && r.token) { setDraft({ ...r.point, note: r.point.note || '', chapter: r.point.chapter || '', section: r.point.section || '', numbers: r.point.numbers || [] }); setToken(r.token) }
    }).catch(e => { if (active) setError(String(e)) })
    return () => { active = false }
  }, [p.editId])
  const run = async (action: () => Promise<void>) => {
    setBusy(true); p.onBusy(true); setError('')
    try { await action() } catch (e) { setError(String(e)) }
    finally { setBusy(false); p.onBusy(false) }
  }
  const save = () => void run(async () => {
    const r = await window.electronAPI.manageKnowledge({ operation: 'update', kp_id: p.editId, token, patch: draft })
    await p.onChanged(); p.onClose(); setDraft(null)
    p.onNotice(r.unchanged ? '内容已是最新。' : `知识点已保存。备份：${r.backup}。可继续核验并生成复习产物。`)
  })
  const prepare = () => void run(async () => {
    setDeletion(await window.electronAPI.manageKnowledge({ operation: 'prepare_delete', ids: p.selected }))
  })
  const remove = () => void run(async () => {
    const r = await window.electronAPI.manageKnowledge({ operation: 'delete', ids: deletion?.ids, delete_token: deletion?.delete_token })
    setDeletion(null); p.onSelect([]); await p.onChanged()
    p.onNotice(`已删除 ${r.deleted?.length} 条知识点。备份：${r.backup}。点击“生成复习产物”更新材料。`)
  })
  const field = (key: keyof KnowledgeDraft, value: unknown) => setDraft(d => d ? { ...d, [key]: value } : d)
  return <>
    <div className="knowledge-bulk-actions">
      <span>已选 {p.selected.length} 条</span>
      <button className="btn btn-secondary" disabled={p.disabled} onClick={() => p.onSelect([...new Set([...p.selected, ...p.visible.map(k => k.kp_id)])])}>选择本页</button>
      <button className="btn btn-secondary" disabled={p.disabled} onClick={() => p.onSelect(p.points.map(k => k.kp_id))}>选择当前筛选结果（{p.points.length}）</button>
      <button className="btn btn-secondary" disabled={p.disabled || !p.selected.length} onClick={() => { p.onSelect([]); setDeletion(null) }}>清空选择</button>
      <button className="btn btn-secondary" disabled={p.disabled || !p.selected.length} onClick={prepare}>删除所选</button>
    </div>
    {error && <p role="alert" className="inline-error">{error}</p>}
    {p.editId && <section ref={editor} className="human-review-panel knowledge-editor" aria-label="编辑知识点">
      <h4>编辑知识点</h4>
      {draft ? <>
        <label>标题<input aria-label="编辑标题" maxLength={150} value={draft.name} disabled={busy} onChange={e => field('name', e.target.value)} /></label>
        <label>正文<textarea aria-label="编辑正文" rows={8} maxLength={30000} value={draft.definition} disabled={busy} onChange={e => field('definition', e.target.value)} /></label>
        <label>批注<textarea aria-label="编辑批注" rows={3} maxLength={5000} value={draft.note} disabled={busy} onChange={e => field('note', e.target.value)} /></label>
        <label>数值（每行一条）<textarea aria-label="编辑数值" value={draft.numbers.join('\n')} disabled={busy} onChange={e => field('numbers', e.target.value.split('\n').filter(v => v.trim()))} /></label>
        <label>重要度<select aria-label="编辑重要度" value={draft.importance} disabled={busy} onChange={e => field('importance', Number(e.target.value))}>{[1,2,3,4,5].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
        <label>章节<input aria-label="编辑章节" value={draft.chapter} disabled={busy} onChange={e => field('chapter', e.target.value)} /></label>
        <label>小节<input aria-label="编辑小节" value={draft.section || ''} disabled={busy} onChange={e => field('section', e.target.value)} /></label>
        <p className="results-hint">更新标题、正文、批注或数值后进入待核验列表。</p>
        <button className="btn btn-primary" disabled={p.disabled || !draft.name.trim() || !draft.definition.trim()} onClick={save}>{busy ? '正在保存…' : '保存修改'}</button>
      </> : <p>正在读取知识点…</p>}
      <button className="btn btn-secondary" disabled={busy} onClick={p.onClose}>取消编辑</button>
    </section>}
    {deletion && <section className="human-review-panel" aria-label="确认批量删除"><h4>删除 {deletion.ids?.length} 条知识点</h4><ul>{deletion.names?.slice(0, 10).map((name, i) => <li key={i}>{name}</li>)}</ul>{(deletion.ids?.length || 0)>10 && <p>共选中 {deletion.ids?.length} 条。</p>}<p>操作会同步移除对应证据和核验记录，并自动创建备份。</p><button className="btn btn-primary" disabled={p.disabled} onClick={remove}>确认删除</button><button className="btn btn-secondary" disabled={busy} onClick={() => setDeletion(null)}>取消删除</button></section>}
  </>
}
