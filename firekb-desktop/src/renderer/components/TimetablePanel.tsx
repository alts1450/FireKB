import { useEffect, useState } from 'react'
import type { Exception, Slot, TimetableState } from '../../types/intake'
import './TimetablePanel.css'

export default function TimetablePanel({ onSaved }: { onSaved: () => void }) {
  const [draft, setDraft] = useState<TimetableState | null>(null)
  const [original, setOriginal] = useState<string[]>([])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  useEffect(() => { void window.electronAPI.intake<TimetableState>('state').then(s => { setDraft(s); setOriginal(s.courses.map(c => c.id)) }).catch(e => setMessage(String(e))) }, [])
  const change = (value: TimetableState) => { setDraft(value); setDirty(true); setMessage('课表有未保存修改') }
  const operation = async (fn: () => Promise<void>) => { setBusy(true); try { await fn() } catch(e) { setMessage(String(e)) } finally { setBusy(false) } }
  if (!draft) return <p className="timetable-message" role="status">{message || '正在读取课表…'}</p>
  const { timetable: t, courses } = draft
  const slot = (i: number, key: keyof Slot, value: string | number) => change({ ...draft, timetable: { ...t, slots: t.slots.map((s,n) => n===i ? { ...s, [key]:value } : s) } })
  const exception = (i: number, key: keyof Exception, value: string) => change({ ...draft, timetable: { ...t, exceptions:t.exceptions.map((s,n)=>n===i?{...s,[key]:value}:s) } })
  const courseSelect = (id: string, onChange: (id: string) => void) => <select aria-label="课程" value={id} onChange={e=>onChange(e.target.value)}>{courses.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select>
  return <div className="timetable-panel">
    <div className="timetable-toolbar"><p>按标题日期与上课时段归课，支持单双周、停课和补课。</p><div><button className="btn btn-secondary" disabled={busy} onClick={()=>void operation(async()=>{const f=await window.electronAPI.readTimetableFile();if(f){const next=await window.electronAPI.intake<TimetableState>('parse',{...f,...draft});change(next);setMessage('已载入课表草稿，请检查后保存')}})}>导入课表</button><button className="btn btn-secondary" disabled={busy} onClick={()=>void operation(async()=>{const path=await window.electronAPI.exportTimetable(draft);if(path)setMessage('课表已导出')})}>导出 JSON</button><button className="btn btn-primary" disabled={busy} onClick={()=>void operation(async()=>{const next=await window.electronAPI.intake<TimetableState>('save',draft);setDraft(next);setOriginal(next.courses.map(c=>c.id));setDirty(false);setMessage('课表已保存，原配置已备份');onSaved()})}>{busy?'请稍候…':'保存课表'}</button></div></div>
    <p className="timetable-hint">CSV / ICS 使用下方的学期设置；JSON 可完整导入课程与学期。第一周须从周一开始。</p>
    <fieldset disabled={busy}><div className="timetable-term">
      <label>第一周 · 周一<input type="date" value={t.semester_start} onChange={e=>change({...draft,timetable:{...t,semester_start:e.target.value}})}/></label>
      <label>总周数<input type="number" min="1" max="60" value={t.total_weeks} onChange={e=>change({...draft,timetable:{...t,total_weeks:Number(e.target.value)}})}/></label>
      <label>时区<select value={t.timezone} onChange={e=>change({...draft,timetable:{...t,timezone:e.target.value}})}>{['Asia/Hong_Kong','Asia/Shanghai','Asia/Taipei','UTC'].map(z=><option key={z}>{z}</option>)}</select></label>
      <label>前后容差 / 分钟<input type="number" min="0" max="60" value={t.tolerance_minutes} onChange={e=>change({...draft,timetable:{...t,tolerance_minutes:Number(e.target.value)}})}/></label>
    </div>
    <div className="timetable-section"><h3>每周课程 <span>{t.slots.length} 个时段</span></h3><div><button className="btn-text" onClick={()=>void operation(async()=>{await window.electronAPI.exportTimetable('\ufeffcourse_id,weekday,start,end,weeks\n'+(courses[0]?.id||'COURSE-1')+',1,08:00,09:40,1-'+t.total_weeks+'\n',true)})}>下载 CSV 模板</button><button className="btn btn-secondary" onClick={()=>change({...draft,timetable:{...t,slots:[...t.slots,{course_id:courses[0]?.id||'',weekday:1,start:'08:00',end:'09:40',weeks:`1-${t.total_weeks}`}]}})}>添加时段</button></div></div>
    <div className="timetable-table"><table><thead><tr><th>课程</th><th>星期</th><th>开始</th><th>结束</th><th>周次</th><th/></tr></thead><tbody>{t.slots.map((s,i)=><tr key={i}><td>{courseSelect(s.course_id,id=>slot(i,'course_id',id))}</td><td><select aria-label="星期" value={s.weekday} onChange={e=>slot(i,'weekday',Number(e.target.value))}>{['一','二','三','四','五','六','日'].map((d,n)=><option key={d} value={n+1}>周{d}</option>)}</select></td><td><input aria-label="开始时间" type="time" value={s.start} onChange={e=>slot(i,'start',e.target.value)}/></td><td><input aria-label="结束时间" type="time" value={s.end} onChange={e=>slot(i,'end',e.target.value)}/></td><td><input aria-label="周次" placeholder="1-16单" value={Array.isArray(s.weeks)?s.weeks.join(','):s.weeks} onChange={e=>slot(i,'weeks',e.target.value)}/></td><td><button className="btn-icon" aria-label="删除时段" onClick={()=>change({...draft,timetable:{...t,slots:t.slots.filter((_,n)=>n!==i)}})}>×</button></td></tr>)}</tbody></table>{!t.slots.length&&<p className="timetable-empty">添加时段，或导入已有的 CSV、ICS、JSON 课表。</p>}</div>
    <p className="timetable-hint">周次可填写 1-16、1-16单、2-16双 或 1,3,5。修改时间不可靠的录音会留待确认。</p>
    <div className="timetable-section"><h3>课程与标题别名</h3><button className="btn btn-secondary" onClick={()=>change({...draft,courses:[...courses,{id:`COURSE-${courses.length+1}`,name:'新课程',aliases:[]}]})}>新增课程</button></div>
    <div className="timetable-table"><table><thead><tr><th>课程 ID</th><th>课程名称</th><th>标题别名 / 逗号分隔</th></tr></thead><tbody>{courses.map((c,i)=><tr key={i}>{(['id','name','aliases'] as const).map(key=><td key={key}><input aria-label={key==='id'?'课程 ID':key==='name'?'课程名称':'标题别名'} readOnly={key!=='aliases'&&original.includes(c.id)} value={key==='aliases'?(c.aliases||[]).join(','):c[key]} onChange={e=>change({...draft,courses:courses.map((row,n)=>n===i?{...row,[key]:key==='aliases'?e.target.value.split(/[,，]/):e.target.value}:row)})}/></td>)}</tr>)}</tbody></table></div>
    <div className="timetable-section"><h3>调课与停课</h3><button className="btn btn-secondary" onClick={()=>change({...draft,timetable:{...t,exceptions:[...t.exceptions,{course_id:courses[0]?.id||'',date:t.semester_start,start:'08:00',end:'09:40',action:'add'}]}})}>添加例外</button></div>
    <div className="timetable-table"><table><thead><tr><th>课程</th><th>日期</th><th>开始</th><th>结束</th><th>处理</th><th/></tr></thead><tbody>{t.exceptions.map((s,i)=><tr key={i}><td>{courseSelect(s.course_id,id=>exception(i,'course_id',id))}</td><td><input type="date" aria-label="调课日期" value={s.date} onChange={e=>exception(i,'date',e.target.value)}/></td><td><input type="time" aria-label="调课开始" value={s.start} onChange={e=>exception(i,'start',e.target.value)}/></td><td><input type="time" aria-label="调课结束" value={s.end} onChange={e=>exception(i,'end',e.target.value)}/></td><td><select aria-label="例外类型" value={s.action} onChange={e=>exception(i,'action',e.target.value)}><option value="add">补课</option><option value="cancel">取消原时段</option></select></td><td><button className="btn-icon" aria-label="删除例外" onClick={()=>change({...draft,timetable:{...t,exceptions:t.exceptions.filter((_,n)=>n!==i)}})}>×</button></td></tr>)}</tbody></table>{!t.exceptions.length&&<p className="timetable-empty">暂无调课或停课安排。</p>}</div>
    </fieldset><p className="timetable-message" role="status">{message || (dirty?'课表有未保存修改':'课表保存在本机，保存前自动备份。')}</p>
  </div>
}
