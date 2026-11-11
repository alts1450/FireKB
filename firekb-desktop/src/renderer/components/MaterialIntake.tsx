import { useEffect, useState } from 'react'
import type { ImportResult, IntakeFile, TimetableState } from '../../types/intake'
import './TimetablePanel.css'

export default function MaterialIntake({ paths, onTimetable }: { paths: string[]; onTimetable: () => void }) {
  const [files,setFiles]=useState<IntakeFile[]>([])
  const [courses,setCourses]=useState<TimetableState['courses']>([])
  const [busy,setBusy]=useState(false)
  const [message,setMessage]=useState('')
  useEffect(()=>{let active=true;void window.electronAPI.intake<TimetableState>('state').then(s=>{if(active)setCourses(s.courses)}).catch(e=>setMessage(String(e)));return()=>{active=false}},[])
  useEffect(()=>{setFiles([]);setMessage('点击“按课表匹配”，预览所选文件或文件夹的归课结果。')},[paths])
  const preview=async()=>{
    setBusy(true);setMessage('正在读取素材并匹配课表…')
    try{const [result,state]=await Promise.all([window.electronAPI.intake<{files:IntakeFile[]}>('preview',{paths}),window.electronAPI.intake<TimetableState>('state')]);setCourses(state.courses);setFiles(result.files.map(f=>({...f,selected:f.status==='matched'})));setMessage(`已读取 ${result.files.length} 个文件。标题时间优先，修改时间及冲突项需确认。`)}catch(e){setMessage(String(e))}finally{setBusy(false)}
  }
  const change=(index:number,key:'selected'|'course_id'|'kind',value:string|boolean)=>setFiles(prev=>prev.map((f,i)=>i===index?{...f,[key]:value,...(key==='course_id'?{selected:!!value,status:'manual'}:{})}:f))
  const selected=files.filter(f=>f.selected&&f.course_id&&!f.done)
  const archive=async()=>{
    setBusy(true);setMessage(`正在复制 ${selected.length} 个文件，原文件保留…`)
    try{const out=await window.electronAPI.intake<{files:ImportResult[]}>('import',{files:selected});const results=new Map(out.files.map(r=>[r.source,r]));setFiles(prev=>prev.map(f=>{const r=results.get(f.path);return r?{...f,status:r.status,done:r.status!=='error',selected:r.status==='error',reason:r.error||r.path||''}:f}));setMessage(`已归档 ${out.files.filter(f=>f.status==='imported').length} 个，重复跳过 ${out.files.filter(f=>f.status==='duplicate').length} 个，失败 ${out.files.filter(f=>f.status==='error').length} 个。`)}catch(e){setMessage(String(e))}finally{setBusy(false)}
  }
  return <div className="intake-preview"><div className="timetable-toolbar"><h3>按课表归类素材</h3><div><button className="btn-text" onClick={onTimetable} disabled={busy}>课表设置</button><button className="btn btn-secondary" onClick={()=>void preview()} disabled={busy||!paths.length}>{busy?'请稍候…':'按课表匹配'}</button></div></div>
    <p className="timetable-hint">支持录音标题日期、课程别名、单双周和调课。先预览，再复制到对应课程目录。</p>
    {files.length>0&&<label className="intake-reason"><input type="checkbox" aria-label="选择全部已指定课程文件" checked={selected.length>0&&selected.length===files.filter(f=>f.course_id&&!f.done).length} disabled={busy} onChange={e=>setFiles(prev=>prev.map(f=>f.course_id&&!f.done?{...f,selected:e.target.checked}:f))}/> 选择全部已指定课程的文件</label>}
    {files.map((f,i)=><div className="intake-row" key={f.path}><input aria-label={`归档 ${f.name}`} type="checkbox" checked={!!f.selected} disabled={busy||f.done} onChange={e=>change(i,'selected',e.target.checked)}/><div className="intake-filename">{f.name}<span className="intake-reason">{({matched:'明确匹配',review:'需确认',conflict:'匹配冲突',unmatched:'未匹配',manual:'手动指定',imported:'已归档',duplicate:'重复跳过',error:'失败'} as Record<string,string>)[f.status]}{f.date?' · '+f.date.slice(0,16).replace('T',' '):''}<br/>{f.reason}</span></div><div><select aria-label={`课程 ${f.name}`} value={f.course_id||''} disabled={busy||f.done} onChange={e=>change(i,'course_id',e.target.value)}><option value="">请选择课程</option>{courses.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select><select aria-label={`类别 ${f.name}`} value={f.kind} disabled={busy||f.done} onChange={e=>change(i,'kind',e.target.value)}>{['录音','课件','规范教材'].map(k=><option key={k}>{k}</option>)}</select></div></div>)}
    <p className="timetable-message" role="status">{message}</p><div className="intake-actions"><span>复制归档 · 内容去重 · 同名保留</span><button className="btn btn-primary" disabled={busy||!selected.length} onClick={()=>void archive()}>确认归档{selected.length?` ${selected.length} 个文件`:''}</button></div>
  </div>
}
