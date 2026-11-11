"""Revision-bound whole-point human approval for the desktop knowledge browser."""
import argparse
import hashlib
import json
import sys
import kb
import review_state
from verify_kp import build_page_index, source_text_of


def load_point(kp_id):
    kps=kb.load_jsonl(kb.KP_FILE);evs=kb.load_jsonl(kb.EVIDENCE_FILE);audits=kb.load_jsonl(kb.AUDIT_FILE)
    kp=next((k for k in kps if k.get('kp_id')==kp_id),None)
    if kp is None:raise ValueError('知识点不存在，请刷新列表')
    evidence=[e for e in evs if e.get('kp_id')==kp_id]
    issues=[a for a in audits if a.get('kp_id')==kp_id and not a.get('superseded')]
    source=source_text_of(kp,evidence,build_page_index())
    revision=review_state.revision(kp,evidence,source)
    token=hashlib.sha256(json.dumps([revision,kp.get('status'),kp.get('human_review'),issues],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return kp,evidence,issues,source,revision,token,kps,evs,audits


def inspect(kp_id):
    kp,evidence,issues,source,revision,token,*_=load_point(kp_id)
    return {'kp_id':kp_id,'name':kp.get('name',''),'definition':kp.get('definition',''),
            'numbers':kp.get('numbers',[]),'note':kp.get('note',''),'source_file':kp.get('source_file',''),
            'source_text':source,'revision':revision,'token':token,'status':kp.get('status'),
            'evidence':[{'quote':e.get('quote',''),'page_label':e.get('page_label','')} for e in evidence],
            'issues':[{'type':a.get('type',''),'detail':a.get('detail','')} for a in issues if a.get('status','pending')=='pending']}


def approve(kp_id, expected_token):
    kp,evidence,issues,source,revision,token,kps,evs,audits=load_point(kp_id)
    if not expected_token or token!=expected_token:raise ValueError('知识点、原文或核验状态已变化，请重新展开核验')
    if kp.get('status')!='pending':raise ValueError('仅待人工处理的知识点可在此核验通过，请刷新列表')
    if any(a.get('human_review',{}).get('verdict')=='bad' for a in issues):raise ValueError('此条存在人工驳回，请先处理驳回记录')
    if not evidence or not source:raise ValueError('缺少本地来源或引用，请先补齐来源后再核验')
    now=kb.now_iso()
    receipt={'verdict':'ok','at':now,'by':'桌面知识点浏览区','revision':revision,'scope':'whole_point'}
    for a in issues:
        if a.get('revision')!=revision:
            # Keep old findings as history; never relabel stale findings as current evidence.
            a['superseded']=True;a['status']='ignored'
        else:
            a['status']='done';a['human_review']=dict(receipt)
    audits.append({'audit_id':'human-'+kp_id+'-'+token[:16],'kp_id':kp_id,'course':kp.get('course',''),
                   'type':'human_confirmation','priority':'P2','target':kp_id,'revision':revision,
                   'status':'done','created_at':now,'human_review':dict(receipt)})
    for e in evidence:
        e.update(human_verified=True,human_verified_at=now,human_revision=revision)
    kp.update(status='verified',verification_method='human',human_review=receipt,verified_at=now)
    kb.backup_files([kb.KP_FILE,kb.EVIDENCE_FILE,kb.AUDIT_FILE],'desktop_human_review')
    kb.atomic_dataset({kb.KP_FILE:kps,kb.EVIDENCE_FILE:evs,kb.AUDIT_FILE:audits})
    return {'kp_id':kp_id,'status':'verified','verification_method':'human','human_review':receipt}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--action',choices=['inspect','approve'],required=True)
    ap.add_argument('--kp-id',required=True);ap.add_argument('--token',default='')
    args=ap.parse_args()
    try:
        with kb.writer_lock():
            result=inspect(args.kp_id) if args.action=='inspect' else approve(args.kp_id,args.token)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
