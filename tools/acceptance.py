"""Reproducible local acceptance checks for a real course and its exported artifacts."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys
import kb
from extract_kp import chunks_from_skeleton
from verify_kp import label_to_page_no, build_page_index, source_text_of
import review_state


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--course', required=True)
    args = ap.parse_args()
    course = kb.resolve_course(args.course)
    out = kb.REVIEW_DIR/kb.course_slug(course)
    checks = []
    def check(name, passed, detail=''):
        checks.append({'name':name,'passed':bool(passed),'detail':detail})
    pages = [p for p in kb.load_jsonl(kb.PAGES_FILE) if p.get('course') == course]
    kps = [k for k in kb.load_jsonl(kb.KP_FILE) if k.get('course') == course]
    ids = {k['kp_id'] for k in kps}
    evs = [e for e in kb.load_jsonl(kb.EVIDENCE_FILE) if e.get('kp_id') in ids]
    page_ids = {(p['source_file'],p['page_no']) for p in pages}
    check('课程文本存在且页主键唯一', bool(pages) and len(page_ids)==len(pages),str(len(pages)))
    raw = {}
    for p in pages: raw[p['source_file']]=p.get('source_sha1')
    bad = []
    for source, expected in raw.items():
        file = kb.ARCHIVE/source
        if not file.is_file(): bad.append(source);continue
        with file.open('rb') as stream: actual=hashlib.file_digest(stream,'sha1').hexdigest()
        if actual != expected: bad.append(source)
    check('解析版本与原始文件哈希一致',not bad, json.dumps(bad,ensure_ascii=False))
    check('知识点和证据主键唯一',len(ids)==len(kps) and len({e['evidence_id'] for e in evs})==len(evs))
    check('所有证据落在本课程真实文件页内',all((e.get('source_file'),label_to_page_no(e.get('page_label'))) in page_ids for e in evs))
    chunks=chunks_from_skeleton(course,pages,kb.load_json(kb.SKELETON_FILE,{}))
    state=kb.load_json(kb.KP_DIR/'state_extract.json',{}).get('done',{})
    missing=[c['chunk_id'] for c in chunks if state.get(c['chunk_id'],{}).get('fp_full') != kb.full_chunk_fingerprint(c)]
    check('当前抽取块完成且全文指纹匹配',bool(chunks) and not missing, f'{len(chunks)-len(missing)}/{len(chunks)}')
    verified={k['kp_id'] for k in kps if k.get('status')=='verified'}
    ev_by_id={e['evidence_id']:e for e in evs}
    index = build_page_index()
    supported = True
    audits=kb.load_jsonl(kb.AUDIT_FILE)
    for k in kps:
        if k['kp_id'] not in verified: continue
        es = [ev_by_id[eid] for eid in k.get('evidence_ids',[]) if eid in ev_by_id]
        rev = review_state.revision(k, es, source_text_of(k,es,index))
        supported &= bool(es) and len(es)==len(k.get('evidence_ids',[])) and review_state.content_decision_accepted(k,audits,rev) and all(review_state.evidence_accepted(e,rev) for e in es)
    check('正文知识点全部有已校验的证据',supported)
    cards=kb.load_jsonl(out/'cards.jsonl')
    check('复习卡仅包含本课程已校验知识点',bool(cards) and all(c.get('kp_id') in verified for c in cards),str(len(cards)))
    try:
        with (out/'05_anki导入.tsv').open(encoding='utf-8-sig',newline='') as f: anki=list(csv.reader(f,delimiter='\t'))
        check('Anki TSV 可解析，字段数和卡片数一致',len(anki)==len(cards) and all(len(row)==4 for row in anki),str(len(anki)))
    except OSError as exc: check('Anki TSV 可读取',False,str(exc))
    manifest=kb.load_json(out/'course-manifest.json',{})
    check('导出清单与当前知识点数量一致',manifest.get('included')==len(verified))
    html=(out/'课程阅读版.html').read_text(encoding='utf-8') if (out/'课程阅读版.html').exists() else ''
    check('HTML 阅读版含全部正文知识点与搜索入口',html.count('<article>')==len(verified) and 'id="search"' in html)
    try:
        from pypdf import PdfReader
        reader=PdfReader(out/'课程知识点归纳.pdf')
        text=''.join(page.extract_text() or '' for page in reader.pages)
        check('PDF 可打开且每条正文知识点可定位',len(reader.pages)>0 and all(kid in text for kid in verified),f'{len(reader.pages)} 页')
    except Exception as exc: check('PDF 可打开',False,str(exc))
    transcript=[t for t in kb.load_jsonl(kb.TRANSCRIPT_FILE) if t.get('course')==course]
    from transcribe import AUDIO_EXTS
    audio=[p for p in (kb.ARCHIVE/'录音').rglob('*') if p.is_file() and p.suffix.lower() in AUDIO_EXTS and course in p.parts]
    check('录音转写保持参考层隔离',(bool(transcript) or not audio) and all(t.get('usage')=='advisory_only' and t.get('weight')==3 for t in transcript)
          and all(not e.get('source_file','').startswith('录音/') for e in evs),str(len(transcript)))
    audio_index=kb.load_json(kb.TEXT_DIR/'transcript_index.json',{})
    incomplete=[]
    for file in audio:
        rel=file.relative_to(kb.ARCHIVE).as_posix()
        meta=audio_index.get(rel,{})
        with file.open('rb') as f: digest=hashlib.file_digest(f,'sha1').hexdigest()
        rows=[t for t in transcript if t['source_file']==rel]
        if meta.get('sha1')!=digest or not rows or meta.get('segments')!=len(rows): incomplete.append(rel)
    check('本课程全部录音均有哈希匹配的完整转写',not incomplete,f'{len(audio)-len(incomplete)}/{len(audio)}')
    pipeline=kb.load_json(kb.LOG_DIR/'pipeline'/f'{kb.course_slug(course)}.json',{})
    required={'parse','ocr','skeleton','extract','verify','review','export','transcribe'}
    if not audio: required.discard('transcribe')
    check('课程全流程各阶段成功完成',required <= set(pipeline.get('completed',[])) and pipeline.get('exit_code')==0)
    report={'course':course,'at':kb.now_iso(),'checks':checks,'all_passed':all(c['passed'] for c in checks),
            'pages_or_paragraphs':len(pages),'source_files':len(raw),'knowledge_points':len(kps),'status':dict(Counter(k.get('status') for k in kps)),
            'evidence':len(evs),'chunks':len(chunks),'cards':len(cards),'transcript_segments':len(transcript),
            'scope':'工程全流程验收；内容核验按条目状态记录'}
    kb.write_json(out/'acceptance.json',report)
    lines=['# 课程全流程验收','',report['scope'],'',f"时间：{report['at']}", '', '| 检查 | 结果 | 说明 |','|---|---|---|']
    lines += [f"| {c['name']} | {'通过' if c['passed'] else '未通过'} | {c['detail']} |" for c in checks]
    lines += ['',f"知识点 {len(kps)} 条，状态 {report['status']}；复习卡 {len(cards)} 张；录音转写 {len(transcript)} 段。",'',
              '待确认内容见 00_待确认清单.md；逐项复核工作表位于上级 review-queue/<课程ID>/P0 和 P1。',
              '内容抽样结果见 08_内容抽样复核.md。AI 复核结果不写成人工裁决。']
    kb.atomic_text(out/'09_验收报告.md','\n'.join(lines))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['all_passed'] else 1


if __name__=='__main__':
    with kb.writer_lock():sys.exit(main())
