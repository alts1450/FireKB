"""Desktop knowledge editing and batch deletion with backups and revision checks."""
import hashlib
import json
import sys
import kb
import review_state
from verify_kp import build_page_index, source_text_of


def token(row):
    return hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def manage(data):
    rows=kb.load_jsonl(kb.KP_FILE);by_id={k['kp_id']:k for k in rows}
    operation=data.get('operation')
    if operation in {'inspect','update'}:
        row=by_id.get(data.get('kp_id'))
        if row is None:raise ValueError('知识点已变化，请刷新列表')
        if operation=='inspect':return {'point':{f:row.get(f,[] if f=='numbers' else 3 if f=='importance' else '') for f in ['name','definition','note','importance','chapter','section','numbers']},'token':token(row)}
        if data.get('token')!=token(row):raise ValueError('知识点已更新，请重新打开编辑')
        patch=data.get('patch',{})
        allowed={'name','definition','note','importance','chapter','section','numbers'}
        if not isinstance(patch,dict) or set(patch)-allowed:raise ValueError('编辑字段无效')
        updated=dict(row)
        for field,value in patch.items():
            if field=='importance':
                if type(value)!=int or not 1<=value<=5:raise ValueError('重要度范围为1至5')
            elif field=='numbers':
                if not isinstance(value,list) or len(value)>200 or any(not isinstance(v,(str,int,float)) or isinstance(v,bool) for v in value):raise ValueError('请检查数值列表')
            else:
                if not isinstance(value,str) or len(value)>({'name':150,'definition':30000,'note':5000}.get(field,500)):raise ValueError('请检查编辑内容长度')
                value=value.strip()
                if field in {'name','definition'} and not value:raise ValueError('标题与正文请填写完整')
            updated[field]=value
        if updated==row:return {'kp_id':row['kp_id'],'unchanged':True}
        # Legacy display aliases must follow the canonical fields.
        if 'title' in updated:updated['title']=updated['name']
        if 'content' in updated:updated['content']=updated['definition']
        updated['edited_at']=kb.now_iso()
        evidence=kb.load_jsonl(kb.EVIDENCE_FILE);audits=kb.load_jsonl(kb.AUDIT_FILE)
        semantic=any(updated.get(f)!=row.get(f) for f in ['name','definition','note','numbers'])
        outputs={kb.KP_FILE:[updated if k['kp_id']==row['kp_id'] else k for k in rows]}
        if semantic:
            updated.update(status='pending',verification_method='human',requires_content_review=True)
            for field in ['human_review','ai_verification','verified_at']:updated.pop(field,None)
            es=[e for e in evidence if e.get('kp_id')==row['kp_id']]
            rev=review_state.revision(updated,es,source_text_of(updated,es,build_page_index()))
            for a in audits:
                if a.get('kp_id')==row['kp_id']:a.update(superseded=True,status='ignored')
            audits.append({'audit_id':'edit-'+row['kp_id']+'-'+token(updated)[:16],'kp_id':row['kp_id'],
                           'course':row.get('course'),'type':'edited_content','priority':'P0','target':row['kp_id'],
                           'revision':rev,'detail':'知识点已编辑，请核验更新后的内容','status':'pending','created_at':updated['edited_at']})
            outputs[kb.AUDIT_FILE]=audits
        backup=kb.backup_files(outputs,'knowledge_edit');kb.atomic_dataset(outputs)
        return {'kp_id':row['kp_id'],'status':updated.get('status'),'backup':str(backup)}
    if operation in {'prepare_delete','delete'}:
        ids=data.get('ids')
        if not isinstance(ids,list) or not ids or len(ids)>10000 or any(not isinstance(i,str) or i not in by_id for i in ids):raise ValueError('所选知识点已变化，请刷新后重新选择')
        ids=list(dict.fromkeys(ids));tokens={i:token(by_id[i]) for i in ids}
        delete_token=hashlib.sha256(json.dumps(tokens,sort_keys=True).encode()).hexdigest()
        if operation=='prepare_delete':return {'ids':ids,'delete_token':delete_token,'names':[by_id[i].get('name','') for i in ids]}
        if data.get('delete_token')!=delete_token:raise ValueError('所选知识点已更新，请重新确认删除')
        chosen=set(ids);outputs={kb.KP_FILE:[k for k in rows if k['kp_id'] not in chosen]}
        for file in [kb.EVIDENCE_FILE,kb.AUDIT_FILE,kb.KP_DIR/'ai_reviews.jsonl',kb.KP_DIR/'quality.jsonl']:
            if file.exists():outputs[file]=[r for r in kb.load_jsonl(file) if r.get('kp_id') not in chosen and (file!=kb.AUDIT_FILE or r.get('target') not in chosen)]
        backup=kb.backup_files(outputs,'knowledge_delete');kb.atomic_dataset(outputs)
        return {'deleted':ids,'backup':str(backup)}
    raise ValueError('未知知识点操作')


if __name__=='__main__':
    try:
        data=json.loads(sys.stdin.read(1_000_000))
        with kb.writer_lock():result=manage(data)
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr);sys.exit(2)
