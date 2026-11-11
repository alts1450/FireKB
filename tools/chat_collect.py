"""Collect explicitly selected chat passages as human-approved network knowledge."""
import hashlib
import json
import re
import sys
import kb
import review_state


def collect(data):
    answer_id=data.get('answer_id','')
    if not isinstance(answer_id,str) or not re.fullmatch(r'[a-f0-9]{32}',answer_id):raise ValueError('请从新生成的回答中收录')
    saved=kb.load_json(kb.LOG_DIR/'chat-answers'/(answer_id+'.json'),{})
    answer=saved.get('answer',{});segments=answer.get('segments',[])
    ranges=data.get('ranges')
    if 'ranges' in data:
        if not isinstance(ranges,list) or not ranges or len(ranges)>500:raise ValueError('请选择有效的回答文字')
        checked=[]
        for r in ranges:
            if not isinstance(r,dict) or any(type(r.get(k))!=int for k in ['index','start','end']):raise ValueError('所选文字位置无效')
            i,start,end=r['index'],r['start'],r['end']
            if not 0<=i<len(segments) or not 0<=start<end<=len(segments[i]['text']):raise ValueError('所选文字已变化，请重新选择')
            checked.append({'index':i,'start':start,'end':end})
        ranges=sorted(checked,key=lambda r:(r['index'],r['start']))
        if any(a['index']==b['index'] and a['end']>b['start'] for a,b in zip(ranges,ranges[1:])):raise ValueError('所选文字范围重叠，请重新选择')
        indices=sorted({r['index'] for r in ranges})
        original='\n\n'.join(segments[r['index']]['text'][r['start']:r['end']] for r in ranges)
    else:
        indices=data.get('indices')
        if not isinstance(indices,list) or not indices or any(type(i)!=int or not 0<=i<len(segments) for i in indices):raise ValueError('请勾选有效的对话段落')
        indices=sorted(set(indices))
        original='\n\n'.join(segments[i]['text'] for i in indices)
    title=str(data.get('title','')).strip();note=str(data.get('note','')).strip()
    if not title or len(title)>150 or len(note)>5000:raise ValueError('请填写150字以内的标题，批注最多5000字')
    course=next((c for c in kb.load_courses() if c.get('id')==data.get('course_id')),None)
    if not course or not re.fullmatch(r'[A-Za-z0-9_-]+',course['id']):raise ValueError('请选择有效课程')
    definition=str(data.get('definition',original)).strip()
    if not definition or len(definition)>30000:raise ValueError('知识点内容须为1至30000字')
    if data.get('confirmed') is not True:raise ValueError('请确认已人工核验所选内容')
    digest=hashlib.sha256(json.dumps([answer_id,ranges if ranges is not None else indices,course['id'],title,definition,note],ensure_ascii=False).encode()).hexdigest()
    kid=f"{course['id']}-CHAT-{digest[:20]}";eid=kid+'-E1'
    kps=kb.load_jsonl(kb.KP_FILE)
    if any(k.get('kp_id')==kid for k in kps):return {'kp_id':kid,'duplicate':True}
    now=kb.now_iso();source=f"网络/{course['id']}/{kid}.md"
    source_text='# 对话收录原始记录\n\n'+json.dumps(saved,ensure_ascii=False,indent=2)+'\n\n## 所选原文\n\n'+original
    page={'course':course['name'],'course_id':course['id'],'source_file':source,'source_kind':'chat_network',
          'file_id':kid,'page_no':1,'page_label':'第1页','text':source_text,'char_count':len(source_text),
          'source_sha1':hashlib.sha1(source_text.encode()).hexdigest()}
    kp={'kp_id':kid,'course':course['name'],'course_id':course['id'],'name':title,'definition':definition,'note':note,
        'numbers':[],'aliases':[],'chapter':'网络与对话批注','section':'人工收录','importance':3,'type':'概念',
        'source_file':source,'source_type':'network','source_label':'网络','origin_kind':'model_conversation',
        'source_urls':answer.get('web_sources',[]),'chat_answer_id':answer_id,'selected_segments':indices,
        'page_label_start':'第1页','page_label_end':'第1页','chunk_id':kb.make_chunk_key(course['name'],1,1,kid),
        'evidence_ids':[eid],'created_at':now,'status':'verified','verification_method':'human'}
    if ranges is not None:kp['selected_ranges']=ranges
    ev={'evidence_id':eid,'kp_id':kid,'course':course['name'],'chunk_id':kp['chunk_id'],'source_file':source,
        'page_label':'第1页','quote':original,'verify_method':'exact','quote_verified':True,
        'evidence_type':'chat_selection','verification_scope':'对话原文一致性核对'}
    revision=review_state.revision(kp,[ev],source_text)
    receipt={'verdict':'ok','at':now,'by':'对话批注收录','revision':revision,'scope':'whole_point'}
    kp.update(human_review=receipt,verified_at=now)
    ev.update(human_verified=True,human_verified_at=now,human_revision=revision)
    audits=kb.load_jsonl(kb.AUDIT_FILE);audits.append({'audit_id':'human-'+kid,'kp_id':kid,'course':course['name'],
        'type':'human_confirmation','priority':'P2','target':kid,'revision':revision,'status':'done','created_at':now,'human_review':receipt})
    files=[kb.KP_FILE,kb.EVIDENCE_FILE,kb.AUDIT_FILE,kb.PAGES_FILE]
    kb.backup_files(files,'chat_collect')
    kb.atomic_text(kb.ARCHIVE/source,source_text)
    kb.atomic_dataset({kb.KP_FILE:kps+[kp],kb.EVIDENCE_FILE:kb.load_jsonl(kb.EVIDENCE_FILE)+[ev],
                       kb.AUDIT_FILE:audits,kb.PAGES_FILE:kb.load_jsonl(kb.PAGES_FILE)+[page]})
    return {'kp_id':kid,'duplicate':False}


if __name__=='__main__':
    try:
        data=json.loads(sys.stdin.read(200000))
        with kb.writer_lock():result=collect(data)
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr);sys.exit(2)
