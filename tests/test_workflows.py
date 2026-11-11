"""Isolated regression tests: user workflows, data integrity, and timetable intake."""
import csv
from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import importlib.util
from unittest import mock
from urllib.request import Request,urlopen
from zoneinfo import ZoneInfo

CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CODE/'tools'))
import kb
import timetable as tt
import verify_kp
import intake_bridge


class Workflows(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='firekb_tests_')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.courses=[{'id':'A','name':'课程甲','aliases':['给水']},{'id':'B','name':'课程乙','aliases':['排烟']}]
        self.write('00_配置/courses.json',{'courses':self.courses})
        self.config={'semester_start':'2026-09-07','total_weeks':17,'timezone':'Asia/Hong_Kong','tolerance_minutes':15,'slots':[{'course_id':'A','weekday':2,'start':'13:30','end':'15:10','weeks':'1-17'}],'exceptions':[]}

    def write(self,rel,data):
        p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');return p

    def jsonl(self,rel,data):
        p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in data),encoding='utf-8');return p

    def run_cli(self,*args,code=0):
        p=subprocess.run([sys.executable,'-B','-m','firekb',*args],cwd=CODE,env={**os.environ,'FIREKB_ROOT':str(self.root)},capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(p.returncode,code,p.stdout+'\n'+p.stderr)
        return p

    def patched(self):
        return mock.patch.multiple(kb,ROOT=self.root,CONFIG_DIR=self.root/'00_配置',ARCHIVE=self.root/'10_原始归档',LOG_DIR=self.root/'90_日志',TEXT_DIR=self.root/'20_文本库',KP_DIR=self.root/'30_知识点',REVIEW_DIR=self.root/'40_复习产物',SKELETON_FILE=self.root/'30_知识点/skeleton.json')

    def test_numeric_boundaries(self):
        for number,body,expected in [('1.5m','15m',False),('35m','350m',False),('-5','5',False),('1.5','.15',False),('1.5m','1.5s',False),('1.5','1.50',True),('0.5','.5',True),('1000','1,000',True),('1.5–2.5','1.5 to 2.5',True),('1.5–2.5','1.5 to 3.5',False)]:
            with self.subTest(number=number,body=body):self.assertEqual(verify_kp.number_appears(number,body),expected)

    def test_filename_and_weeks(self):
        cfg=tt.validate(self.config,self.courses)
        for name in ['9月22日 13点59分.aac','20260922_135900.aac','2026-09-22 13.59.00.aac']:
            result=tt.classify({'name':name},cfg,self.courses)
            self.assertEqual(result['course_id'],'A',result);self.assertEqual(result['status'],'matched')
        cfg['slots'][0]['weeks']=[1,3,5]
        self.assertEqual(tt.classify({'name':'9月15日 13点59分.aac'},cfg,self.courses)['status'],'unmatched')

    def test_conflicts_and_metadata_fallback(self):
        cfg=tt.validate(self.config,self.courses)
        result=tt.classify({'name':'排烟 9月22日 13点59分.aac'},cfg,self.courses)
        self.assertEqual(result['status'],'conflict');self.assertIsNone(result['course_id'])
        stamp=datetime(2026,9,22,13,59,tzinfo=ZoneInfo('Asia/Hong_Kong')).timestamp()*1000
        self.assertEqual(tt.classify({'name':'录音.aac','last_modified':stamp},cfg,self.courses)['status'],'review')
        cfg['slots'].append({**cfg['slots'][0],'course_id':'B'})
        self.assertEqual(tt.classify({'name':'9月22日 13点59分.aac'},cfg,self.courses)['status'],'conflict')

    def test_holidays_and_makeup(self):
        cfg=tt.validate(self.config,self.courses)
        cfg['exceptions']=[{'course_id':'A','date':'2026-09-22','start':'13:30','end':'15:10','action':'cancel'},{'course_id':'A','date':'2026-09-23','start':'08:00','end':'09:40','action':'add'}]
        self.assertIsNone(tt.classify({'name':'9月22日 13点59分.aac'},cfg,self.courses)['course_id'])
        self.assertEqual(tt.classify({'name':'9月23日 08点02分.aac'},cfg,self.courses)['course_id'],'A')

    def test_import_csv_json_ics(self):
        csv_text='课程,星期,开始,结束,周次\n课程甲,二,13:30,15:10,1-17单\n'
        cfg,_=tt.parse_import(csv_text,'课表.csv',self.config,self.courses)
        self.assertEqual(cfg['slots'][0]['weeks'],list(range(1,18,2)))
        rt,_=tt.parse_import(json.dumps({'timetable':cfg,'courses':self.courses}),'课表.json',{},[])
        self.assertEqual(rt,cfg)
        ics='BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:课程甲\nDTSTART;TZID=Asia/Shanghai:20260908T133000\nDTEND;TZID=Asia/Shanghai:20260908T151000\nRRULE:FREQ=WEEKLY;COUNT=3\nEXDATE;TZID=Asia/Shanghai:20260915T133000\nEND:VEVENT\nEND:VCALENDAR'
        imported,_=tt.parse_import(ics,'课表.ics',self.config,self.courses)
        self.assertEqual([x['date'] for x in imported['exceptions']],['2026-09-08','2026-09-22'])
        with self.assertRaises(ValueError):tt.parse_import(ics.replace('WEEKLY','MONTHLY'),'x.ics',self.config,self.courses)

    def test_archive_duplicate_and_collision(self):
        one=self.root/'source1.aac';one.write_bytes(b'first recording')
        two=self.root/'source2.aac';two.write_bytes(b'second recording')
        with self.patched():
            a=tt.archive_file(one,'9月22日.aac','A','录音')
            b=tt.archive_file(one,'renamed.aac','A','录音')
            c=tt.archive_file(two,'9月22日.aac','A','录音')
        self.assertEqual(a['status'],'imported');self.assertEqual(b['status'],'duplicate');self.assertNotEqual(a['path'],c['path']);self.assertTrue(one.exists())

    def seed_review(self, full_page=False):
        self.jsonl('20_文本库/pages.jsonl',[{'course':'课程甲','source_file':'课件/课程甲/a.pdf','file_id':'f1','page_no':1,'char_count':330 if full_page else 33,'page_label':'第1页','text':'房间的通风开口与排烟系统共同影响空间烟气的流动和分布特征。'*(10 if full_page else 1)}])
        base={'course':'课程甲','course_id':'A','kp_id':'A-KP-0001','name':'空间烟气流动特征','definition':'房间的通风开口与排烟系统共同影响空间烟气的流动和分布特征。','numbers':[],'source_file':'课件/课程甲/a.pdf','page_label_start':'第1页','page_label_end':'第1页','status':'pending','evidence_ids':['e1','e2'],'importance':3,'type':'概念','chapter':'第一章','section':'概述','node_id':'A::1','chunk_id':'课程甲::p1-1'}
        self.jsonl('30_知识点/kp.jsonl',[base])
        self.jsonl('30_知识点/evidence.jsonl',[{'evidence_id':e,'kp_id':base['kp_id'],'course':'课程甲','quote':'不存在的引用 '+e,'verify_method':'not_found','quote_verified':False,'source_file':base['source_file'],'page_label':'第1页','chunk_id':base['chunk_id']} for e in ['e1','e2']])
        self.jsonl('30_知识点/audit.jsonl',[{'audit_id':'other','kp_id':'B-KP-0001','course':'课程乙','priority':'P0','status':'pending','type':'no_evidence','target':'B-KP-0001'}])
        return base

    def decision(self,audit,verdict):
        p=self.root/'decision.tsv'
        with p.open('w',encoding='utf-8',newline='') as f:
            wr=csv.DictWriter(f,fieldnames=['audit_id','kp_id','裁决(ok/bad/skip)'],delimiter='\t');wr.writeheader();wr.writerow({'audit_id':audit['audit_id'],'kp_id':audit['kp_id'],'裁决(ok/bad/skip)':verdict})
        return p

    def human_call(self, action, kp_id, token='', code=0):
        result=subprocess.run([sys.executable,'-B',str(CODE/'tools/human_review.py'),'--action',action,'--kp-id',kp_id,'--token',token],
                              cwd=CODE,env={**os.environ,'FIREKB_ROOT':str(self.root),'PYTHONUTF8':'1'},capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,code,result.stdout+'\n'+result.stderr)
        return json.loads(result.stdout) if code==0 else result.stderr

    def test_desktop_human_approval_persists_through_verify_and_review(self):
        k=self.seed_review(full_page=True);self.run_cli('verify','--course','A')
        detail=self.human_call('inspect',k['kp_id'])
        self.assertTrue(detail['source_text']);self.assertEqual(len(detail['evidence']),2)
        receipt=self.human_call('approve',k['kp_id'],detail['token'])
        self.assertEqual(receipt['verification_method'],'human')
        ai={**AiReviewTests().receipt(),'kp_id':k['kp_id'],'revision':detail['revision']}
        self.write('receipt.json',ai)
        result=subprocess.run([sys.executable,'-B','-c',"import sys;sys.path.insert(0,'tools');import kb,ai_review;ai_review.apply_results([kb.load_json(kb.ROOT/'receipt.json')])"],
                              cwd=CODE,env={**os.environ,'FIREKB_ROOT':str(self.root),'PYTHONUTF8':'1'},capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]['verification_method'],'human')
        self.run_cli('verify','--course','A','--recheck')
        saved=kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]
        self.assertEqual(saved['status'],'verified');self.assertEqual(saved['human_review'],receipt['human_review'])
        evidence=kb.load_jsonl(self.root/'30_知识点/evidence.jsonl')
        self.assertTrue(all(e['human_verified'] for e in evidence));self.assertFalse(any(e['quote_verified'] for e in evidence))
        self.run_cli('review','--course','A')
        self.assertIn('人工核验',(self.root/'40_复习产物/A/02_背诵卡.md').read_text(encoding='utf-8'))
        audits=kb.load_jsonl(self.root/'30_知识点/audit.jsonl')
        self.assertEqual(next(a for a in audits if a['audit_id']=='other')['status'],'pending')

    def test_desktop_human_stale_preview_and_duplicate_submit_do_not_write(self):
        k=self.seed_review(full_page=True);self.run_cli('verify','--course','A')
        detail=self.human_call('inspect',k['kp_id'])
        path=self.root/'30_知识点/kp.jsonl';rows=kb.load_jsonl(path);rows[0]['definition']+='新增条件'
        self.jsonl('30_知识点/kp.jsonl',rows);before=path.read_bytes()
        self.assertIn('已变化',self.human_call('approve',k['kp_id'],detail['token'],code=2));self.assertEqual(before,path.read_bytes())
        detail=self.human_call('inspect',k['kp_id']);self.human_call('approve',k['kp_id'],detail['token'])
        before=path.read_bytes();self.human_call('approve',k['kp_id'],detail['token'],code=2);self.assertEqual(before,path.read_bytes())

    def test_desktop_human_source_change_invalidates_export_decision(self):
        import review_state
        k=self.seed_review(full_page=True);self.run_cli('verify','--course','A')
        detail=self.human_call('inspect',k['kp_id']);self.human_call('approve',k['kp_id'],detail['token'])
        saved=kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]
        self.assertTrue(review_state.content_decision_accepted(saved,[],detail['revision']))
        self.assertFalse(review_state.content_decision_accepted(saved,[],'changed'))
        pages=kb.load_jsonl(self.root/'20_文本库/pages.jsonl');pages[0]['text']+='新增原文'
        self.jsonl('20_文本库/pages.jsonl',pages)
        self.run_cli('verify','--course','A','--recheck')
        self.assertEqual(kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]['status'],'pending')

    def test_desktop_human_rejection_cannot_be_silently_overridden(self):
        k=self.seed_review(full_page=True);self.run_cli('verify','--course','A')
        path=self.root/'30_知识点/audit.jsonl';audits=kb.load_jsonl(path)
        next(a for a in audits if a['kp_id']==k['kp_id'])['human_review']={'verdict':'bad'}
        self.jsonl('30_知识点/audit.jsonl',audits)
        detail=self.human_call('inspect',k['kp_id']);before=path.read_bytes()
        self.assertIn('驳回',self.human_call('approve',k['kp_id'],detail['token'],code=2));self.assertEqual(before,path.read_bytes())

    def test_review_decisions_scope_and_content_change(self):
        kp=self.seed_review();self.run_cli('verify','--course','A')
        audit=kb.load_jsonl(self.root/'30_知识点/audit.jsonl')
        self.assertTrue(any(a['audit_id']=='other' for a in audit))
        issue=next(a for a in audit if a.get('target')=='e1')
        self.run_cli('queue','--apply',str(self.decision(issue,'ok')),'--yes')
        ev=kb.load_jsonl(self.root/'30_知识点/evidence.jsonl')
        self.assertTrue(ev[0].get('human_verified'));self.assertFalse(ev[1].get('human_verified',False))
        self.assertEqual(kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]['status'],'pending')
        self.run_cli('verify','--course','A')
        issue2=next(a for a in kb.load_jsonl(self.root/'30_知识点/audit.jsonl') if a.get('target')=='e2' and not a.get('superseded'))
        self.run_cli('queue','--apply',str(self.decision(issue2,'bad')),'--yes');self.run_cli('verify','--course','A')
        self.assertEqual(kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]['status'],'rejected')
        kp['definition']+='内容已更新。';self.jsonl('30_知识点/kp.jsonl',[kp])
        self.run_cli('queue','--apply',str(self.decision(issue,'ok')),'--yes',code=3)
        self.run_cli('verify','--course','A')
        self.assertEqual(kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]['status'],'pending')

    def test_writer_lock_blocks_other_thread(self):
        failures=[]
        with self.patched(),kb.writer_lock():
            def attempt():
                try:
                    with kb.writer_lock():failures.append('unexpected entry')
                except RuntimeError:pass
            t=threading.Thread(target=attempt);t.start();t.join()
        self.assertEqual(failures,[])

    def test_all_human_confirmed_evidence_can_enter_review_but_stale_content_cannot(self):
        self.seed_review();self.run_cli('verify','--course','A')
        for target in ['e1','e2']:
            issue=next(a for a in kb.load_jsonl(self.root/'30_知识点/audit.jsonl') if a.get('target')==target and not a.get('superseded'))
            self.run_cli('queue','--apply',str(self.decision(issue,'ok')),'--yes')
        self.run_cli('review','--course','A')
        self.assertEqual(len(kb.load_jsonl(self.root/'40_复习产物/A/cards.jsonl')),1)
        source=self.root/'10_原始归档/课件/课程甲/a.pdf'
        source.parent.mkdir(parents=True,exist_ok=True)
        source.write_bytes(b'%PDF-1.4\n% human-reviewed source fixture')
        self.run_cli('export','--course','A')
        kp=kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]
        kp['definition']+='新版本内容。';self.jsonl('30_知识点/kp.jsonl',[kp])
        self.run_cli('review','--course','A')
        self.assertFalse(kb.load_jsonl(self.root/'40_复习产物/A/cards.jsonl'))

    def test_desktop_bridge_import(self):
        self.write('00_配置/timetable.json', tt.validate(self.config,self.courses))
        source=self.root/'9月22日 13点59分.aac';source.write_bytes(b'test-only recording')
        with self.patched(),mock.patch.object(tt,'FILE',self.root/'00_配置/timetable.json'):
            preview=intake_bridge.handle('preview',{'paths':[str(source)]})
            self.assertEqual(preview['files'][0]['course_id'],'A')
            result=intake_bridge.handle('import',{'files':preview['files']})
            self.assertEqual(result['files'][0]['status'],'imported')
            self.assertTrue(source.exists())

    def test_dataset_interruption_recovers_all_files(self):
        a=self.root/'30_知识点/kp.jsonl'; b=self.root/'30_知识点/evidence.jsonl'
        self.jsonl('30_知识点/kp.jsonl',[{'old':True}]);self.jsonl('30_知识点/evidence.jsonl',[{'old':True}])
        real_replace=os.replace
        def interrupted(src,dst):
            if Path(dst)==b:raise OSError('simulated power loss')
            return real_replace(src,dst)
        with self.patched():
            with mock.patch.object(os,'replace',side_effect=interrupted):
                with self.assertRaises(OSError):kb.atomic_dataset({a:[{'new':'kp'}],b:[{'new':'evidence'}]})
            self.assertTrue((self.root/'.firekb-transaction.json').exists())
            kb.recover_dataset()
            self.assertEqual(kb.load_jsonl(a),[{'new':'kp'}]);self.assertEqual(kb.load_jsonl(b),[{'new':'evidence'}])
            self.assertFalse((self.root/'.firekb-transaction.json').exists())

    @unittest.skipUnless(importlib.util.find_spec('reportlab'), 'optional reportlab fixture dependency')
    def test_parser_replaces_changed_file_and_keeps_other_course(self):
        from reportlab.pdfgen.canvas import Canvas
        def pdf(course,text):
            p=self.root/'10_原始归档'/'课件'/course/'lesson.pdf';p.parent.mkdir(parents=True,exist_ok=True)
            c=Canvas(str(p));c.drawString(60,700,text);c.save()
        pdf('课程甲','First version');pdf('课程乙','Other course remains')
        self.run_cli('parse')
        initial=kb.load_jsonl(self.root/'20_文本库/pages.jsonl'); other=[p for p in initial if p['course']=='课程乙']
        pdf('课程甲','Updated version')
        self.run_cli('parse','--course','A')
        after=kb.load_jsonl(self.root/'20_文本库/pages.jsonl')
        self.assertEqual(len(after),2);self.assertEqual(other,[p for p in after if p['course']=='课程乙'])
        self.assertIn('Updated version',next(p['text'] for p in after if p['course']=='课程甲'))
        self.run_cli('parse','--course','A');self.assertEqual(len(kb.load_jsonl(self.root/'20_文本库/pages.jsonl')),2)

    def test_pipeline_stops_and_resumes_after_failure(self):
        import pipeline
        self.write('30_知识点/skeleton.json',{'courses':{'课程甲':{'chapters':[]}}})
        calls=[]
        def execute(cmd,**kwargs):
            stage=cmd[4];calls.append(stage);return subprocess.CompletedProcess(cmd,4 if stage=='ocr' else 0)
        with self.patched(),mock.patch.object(sys,'argv',['pipeline','--course','A']),mock.patch.object(pipeline.subprocess,'run',side_effect=execute):
            self.assertEqual(pipeline.main(),4)
        self.assertEqual(calls,['parse','ocr'])
        calls.clear()
        def success(cmd,**kwargs):calls.append(cmd[4]);return subprocess.CompletedProcess(cmd,0)
        with self.patched(),mock.patch.object(sys,'argv',['pipeline','--course','A','--resume']),mock.patch.object(pipeline.subprocess,'run',side_effect=success):
            self.assertEqual(pipeline.main(),0)
        self.assertEqual(calls,['ocr','skeleton','extract','verify','review','export'])

    def test_full_fingerprint_detects_equal_length_middle_edit(self):
        a={'text':'a'*40+'middle A'+'z'*40,'page_start':1,'page_end':1}
        b={**a,'text':'a'*40+'middle B'+'z'*40}
        self.assertEqual(kb.chunk_fingerprint(a),kb.chunk_fingerprint(b))
        self.assertNotEqual(kb.full_chunk_fingerprint(a),kb.full_chunk_fingerprint(b))

    def test_writer_lock_child_inherits_ownership_and_stranger_is_blocked(self):
        child = "import sys;sys.path.insert(0,'tools');import kb;\nwith kb.writer_lock(): print('CHILD_OK')"
        with self.patched(), kb.writer_lock():
            env = {**os.environ, 'FIREKB_ROOT':str(self.root)}
            result = subprocess.run([sys.executable,'-B','-c',child],cwd=CODE,env=env,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('CHILD_OK',result.stdout)
            env.pop('FIREKB_WRITER_TOKEN',None)
            outsider = subprocess.run([sys.executable,'-B','-c',child],cwd=CODE,env=env,capture_output=True,text=True,encoding='utf-8')
            self.assertNotEqual(outsider.returncode,0)

    def test_chapters_do_not_cross_source_files(self):
        from extract_kp import chunks_from_skeleton
        pages = [{'file_id':fid,'source_file':fid+'.pdf','page_no':1,'page_label':'第1页','text':text*300} for fid,text in [('a','甲'),('b','乙')]]
        skel = {'courses':{'课程甲':{'file_scoped':True,'chapters':[
            {'title':'甲章','source_file':'a.pdf','page_start':1,'page_end':1},
            {'title':'乙章','source_file':'b.pdf','page_start':1,'page_end':1}]}}}
        chunks = chunks_from_skeleton('课程甲',pages,skel)
        self.assertEqual([(c['chapter'],c['source_file']) for c in chunks],[('甲章','a.pdf'),('乙章','b.pdf')])

    def test_docx_paragraphs_and_tables_have_stable_locations(self):
        import zipfile
        from parse_docs import parse_docx
        path=self.root/'习题.docx'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>第一题</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格中的题</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        rows,warnings=parse_docx(path)
        self.assertFalse(warnings)
        self.assertEqual([r['text'] for r in rows],['第一题','表格中的题'])
        self.assertEqual([r['page_label'] for r in rows],['第1段','第2段'])

    def test_partial_quote_stays_out_of_verified_material(self):
        import review_state
        self.assertEqual(review_state.kp_status('A',[{'kp_id':'A','priority':'P1','type':'quote_partial','status':'pending'}]),'pending')

    def test_differing_numeric_conditions_are_not_merged(self):
        from make_review import cross_book_dedup
        values=[{'kp_id':'a','course':'甲','name':'消防水箱','numbers':['12m³']},{'kp_id':'b','course':'甲','name':'消防水箱','numbers':['18m³']}]
        kept,removed=cross_book_dedup(values,{})
        self.assertEqual(removed,0);self.assertEqual(len(kept),2)

    def test_unicode_line_separators_are_inside_jsonl_records(self):
        import schema_check
        path=self.jsonl('pages.jsonl',[{'text':'首行\u2028续行\u2029结尾'},{'text':'第二条'}])
        rows,errors=schema_check._rows(path)
        self.assertFalse(errors);self.assertEqual(len(rows),2)

    def test_slide_references_do_not_use_book_page_offsets(self):
        from make_review import short_src
        with self.patched():
            self.assertIn('幻灯片 3',short_src({'source_file':'课件/甲/课程.pptx','course':'课程甲','page_label_start':'第3张','page_label_end':'第3张'}))

    def test_number_on_unrelated_page_does_not_validate_claim(self):
        kp={'source_file':'a.pdf','page_label_start':'第1页','page_label_end':'第2页'}
        index={('a.pdf',1):{'text':'容积 18m²（OCR 错误）'},('a.pdf',2):{'text':'另一张表的容积 18m³'}}
        text=verify_kp.numeric_source_text_of(kp,[{'page_label':'第1页'}],index)
        self.assertFalse(verify_kp.number_appears('18m³',text))

    def test_new_source_append_preserves_reviewed_chapters_and_is_idempotent(self):
        from build_skeleton import append_new_sources
        old={'file_scoped':True,'chapters':[{'title':'已审定章节','source_file':'old.pdf','page_start':1,'page_end':3,'sections':[]}]}
        pages=[{'source_file':'old.pdf','source_kind':'pdf','file_id':'old','page_no':1,'text':'旧页'},
               {'source_file':'new.pptx','source_kind':'pptx','file_id':'new','page_no':1,'text':'新课件'}]
        result,changed=append_new_sources('课程甲',pages,old)
        self.assertTrue(changed);self.assertEqual(result['chapters'][0],old['chapters'][0]);self.assertEqual(len(old['chapters']),1)
        again,changed=append_new_sources('课程甲',pages,result)
        self.assertFalse(changed);self.assertEqual(again,result)

    def test_hashed_backup_can_restore_binary_file(self):
        import maintenance
        path=self.root/'20_文本库/sample.bin'
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'\x00original\xff')
        with self.patched():
            backup=kb.backup_files([path],'restore-test')
            self.assertEqual(len(maintenance.verify(backup)),1)
            path.write_bytes(b'changed')
            with mock.patch.object(sys,'argv',['maintenance','--restore',str(backup),'--yes']):
                self.assertEqual(maintenance.main(),0)
        self.assertEqual(path.read_bytes(),b'\x00original\xff')

    def test_course_names_cannot_escape_archive_categories(self):
        with self.patched():
            with self.assertRaises(ValueError):
                tt.save(self.config,self.courses+[{'id':'C','name':'../越界'}])

    def test_math_layout_is_idempotent_and_keeps_radical_fraction_grouping(self):
        from math_format import format_text, html_text
        text='管径 D = √(4Q/(πV))，高度 h≥(P2+P4)/10，条件保持原样。'
        formatted=format_text(text)
        self.assertIn(r'\sqrt{\frac{4Q}{\pi V}}',formatted)
        self.assertEqual(format_text(formatted),formatted)
        self.assertIn('条件保持原样。',formatted)
        self.assertIn('&lt;script&gt;',html_text('<script>$x^2$'))

    def apply_ai_fixture(self):
        receipt=AiReviewTests().receipt();receipt['kp_id']='A-KP-0001'
        self.write('receipt.json',receipt)
        script="import kb,ai_review,review_state;from verify_kp import build_page_index,source_text_of;k=kb.load_jsonl(kb.KP_FILE)[0];es=kb.load_jsonl(kb.EVIDENCE_FILE);r=kb.load_json(kb.ROOT/'receipt.json');r['revision']=review_state.revision(k,es,source_text_of(k,es,build_page_index()));ai_review.apply_results([r])"
        result=subprocess.run([sys.executable,'-B','-c',script],cwd=CODE/'tools',env={**os.environ,'FIREKB_ROOT':str(self.root)},capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_ai_decision_survives_verify_and_enters_cards_until_content_changes(self):
        self.seed_review();self.run_cli('verify','--course','A');self.apply_ai_fixture()
        self.run_cli('verify','--course','A');self.run_cli('review','--course','A')
        kp_path=self.root/'30_知识点/kp.jsonl';kps=kb.load_jsonl(kp_path)
        self.assertEqual(kps[0]['status'],'verified')
        evidence=kb.load_jsonl(self.root/'30_知识点/evidence.jsonl')
        self.assertTrue(all(not e.get('quote_verified') and not e.get('human_verified') for e in evidence))
        self.assertEqual(len(kb.load_jsonl(self.root/'40_复习产物/A/cards.jsonl')),1)
        kps[0]['definition']+='额外条件已经改变。';kb.rewrite_jsonl(kp_path,kps)
        self.run_cli('review','--course','A')
        self.assertEqual(kb.load_jsonl(self.root/'40_复习产物/A/cards.jsonl'),[])
        self.run_cli('verify','--course','A');self.assertEqual(kb.load_jsonl(kp_path)[0]['status'],'pending')

    def test_ai_cannot_override_human_rejection(self):
        self.seed_review();self.run_cli('verify','--course','A')
        issue=next(a for a in kb.load_jsonl(self.root/'30_知识点/audit.jsonl') if a.get('target')=='e1')
        self.run_cli('queue','--apply',str(self.decision(issue,'bad')),'--yes');self.apply_ai_fixture()
        self.assertEqual(kb.load_jsonl(self.root/'30_知识点/kp.jsonl')[0]['status'],'rejected')
        self.assertTrue(all(not e.get('ai_verification') for e in kb.load_jsonl(self.root/'30_知识点/evidence.jsonl')))

    @unittest.skipUnless(importlib.util.find_spec('matplotlib'),'PDF math requires full dependencies')
    def test_pdf_variable_explanations_stay_in_one_paragraph(self):
        from export_course import pdf_definition
        from reportlab.lib.styles import getSampleStyleSheet
        with self.patched():
            story=pdf_definition('其中 $C$ 为系数，$d$ 为内径。',getSampleStyleSheet())
        self.assertEqual(len(story),1)


class AiReviewTests(unittest.TestCase):
    def fixture(self):
        import ai_review
        claim={'id':'definition','kind':'definition','text':'消防水池用于储存消防用水并保证持续供水。'}
        sources=[{'id':'S1','url':'https://www.example.gov.cn/fire','title':'规范原文','authority':4,'content':claim['text']},
                 {'id':'S2','url':'https://www.example.edu.cn/lesson','title':'课程讲义','authority':4,'content':claim['text']+'适用于本章课程。'}]
        judgment={'checks':[{'claim_id':'definition','supported':True,'proofs':[{'source_id':s['id'],'quote':claim['text']} for s in sources]}],
                  **{k:True for k in ['definition_fully_supported','numbers_units_checked','formula_checked','scope_clear','no_conflict','all_claims_covered']},'scope':'general_principle'}
        import copy
        return ai_review,[claim],sources,judgment,copy.deepcopy(judgment)

    def receipt(self):
        from datetime import timedelta,timezone
        module,claims,sources,a,b=self.fixture()
        return {**module.score_evidence(claims,sources,'',a,b),'revision':'current','valid_until':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()}

    def test_two_independent_web_judgments_can_approve(self):
        import review_state
        self.assertTrue(review_state.ai_approved(self.receipt(),'current'))

    def test_invented_quote_cannot_pass(self):
        m,c,s,a,b=self.fixture();b['checks'][0]['proofs']=[{'source_id':'S1','quote':'这是网上从未出现的虚构引文，不能通过核验。'}]
        self.assertEqual(m.score_evidence(c,s,'',a,b)['status'],'needs_human')

    def test_same_site_and_mirrors_do_not_count_twice(self):
        m,c,s,a,b=self.fixture();s[1]['url']='https://m.example.gov.cn/copy'
        self.assertEqual(m.score_evidence(c,s,'',a,b)['components']['independence'],0.5)
        s[1]['url']='https://other.gov.cn/copy';s[1]['content']=s[0]['content']
        self.assertEqual(m.score_evidence(c,s,'',a,b)['components']['independence'],0.5)
        self.assertEqual(m.score_evidence(c,s,'',a,b)['status'],'approved')

    def test_unsupported_number_blocks_high_score(self):
        m,c,s,a,b=self.fixture();c.append({'id':'number_0','kind':'number','text':'50mm'})
        for j in [a,b]:j['checks'].append({'claim_id':'number_0','supported':True,'proofs':[{'source_id':'S1','quote':s[0]['content']}]})
        result=m.score_evidence(c,s,'',a,b)
        self.assertFalse(result['gates']['full_coverage']);self.assertEqual(result['status'],'needs_human')

    def test_local_only_stays_manual_but_primary_domain_is_not_mandatory(self):
        m,c,s,a,b=self.fixture()
        for j in [a,b]:j['checks'][0]['proofs']=[{'source_id':'LOCAL','quote':c[0]['text']}]
        self.assertEqual(m.score_evidence(c,s,c[0]['text'],a,b)['status'],'needs_human')
        m,c,s,a,b=self.fixture()
        for i,source in enumerate(s):source['url']=f'https://blog{i}.com/page'
        self.assertEqual(m.score_evidence(c,s,'',a,b)['status'],'approved')

    def test_local_course_note_and_compatible_scope_can_pass(self):
        m,c,s,a,b=self.fixture();local='本课程例题选用管道直径50mm作为计算条件。'
        c.append({'id':'number_0','kind':'number','text':'50mm'})
        for j in [a,b]:j['checks'].append({'claim_id':'number_0','supported':True,'proofs':[{'source_id':'LOCAL','quote':'50mm'}]})
        b['scope']='course_material'
        r=m.score_evidence(c,s,local,a,b)
        self.assertEqual(r['status'],'approved');self.assertEqual(r['scope'],'course_material')
        for j in [a,b]:j['scope']='current_standard'
        r=m.score_evidence(c,s,local,a,b)
        self.assertFalse(r['gates']['standard_web_coverage']);self.assertEqual(r['status'],'needs_human')

    def test_reliable_international_sources_recognized(self):
        m,*_=self.fixture()
        for domain in ['ws.moi.gov.tw','publications.iafss.org','research.ed.ac.uk']:
            self.assertEqual(m.source_quality({'url':'https://'+domain+'/paper'}),(1.0,True))
        self.assertFalse(m.source_quality({'url':'https://iafss.org.fake.example/paper'})[1])

    def test_version_disagreement_and_missing_conditions_block(self):
        m,c,s,a,b=self.fixture();b['scope']='historical_standard'
        self.assertEqual(m.score_evidence(c,s,'',a,b)['status'],'needs_human')
        b['scope']=a['scope'];b['scope_clear']=False
        self.assertEqual(m.score_evidence(c,s,'',a,b)['status'],'needs_human')

    def test_receipt_threshold_recomputed_not_trusted(self):
        import review_state
        r=self.receipt();r['components']['coverage']=0.5;r['score']=1
        self.assertFalse(review_state.ai_approved(r,'current'))
        r=self.receipt();r['components']['authority']=0;r['score']=0.8
        self.assertTrue(review_state.ai_approved(r,'current'))
        r['components']['independence']=0.999;r['score']=0.8
        self.assertFalse(review_state.ai_approved(r,'current'))

    def test_legacy_receipt_keeps_original_weights_and_threshold(self):
        import review_state
        r=self.receipt();r['policy']='ai-web-v1'
        for key in ['web_corroboration','compatible_scope','standard_web_coverage']:r['gates'].pop(key)
        r['gates'].update(primary_source=True,two_evidence_origins=True,same_scope=True)
        r['components']['authority']=0.6;r['score']=0.9
        self.assertTrue(review_state.ai_approved(r,'current'))
        r['components']['authority']=0.59;r['score']=review_state.ai_score(r['components'],'ai-web-v1')
        self.assertFalse(review_state.ai_approved(r,'current'))

    def test_offline_rescore_is_revision_bound_and_never_extends_expiry(self):
        m,c,s,a,b=self.fixture();record={**self.receipt(),'kp_id':'k','judgments':[a,b],'source_snapshot':s}
        k={'kp_id':'k','course':'A','definition':c[0]['text']}
        def load(path):return [k] if path==kb.KP_FILE else [{'kp_id':'k'}]
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(kb,'LOG_DIR',Path(tmp)),mock.patch.object(kb,'load_jsonl',side_effect=load),mock.patch.object(m,'build_page_index',return_value={}),mock.patch.object(m,'source_text_of',return_value='local'),mock.patch.object(m.review_state,'revision',return_value='current'),mock.patch.object(m.deepseek_web,'search',side_effect=AssertionError('paid call')),mock.patch.object(m.llm,'chat',side_effect=AssertionError('paid call')):
            rows,stats=m.rescore_cached([record],'A')
            self.assertEqual(stats['approved'],1);self.assertEqual(rows[0]['valid_until'],record['valid_until'])
            self.assertEqual(m.rescore_cached([record],'B')[0],[record])
            changed={**record,'revision':'old'}
            self.assertEqual(m.rescore_cached([changed],'A')[1]['changed_or_missing_source'],1)
            expired={**record,'valid_until':'2000-01-01T00:00:00+00:00'}
            self.assertEqual(m.rescore_cached([expired],'A')[1]['expired'],1)

    def test_expired_or_changed_receipt_cannot_accept_evidence(self):
        import review_state
        r=self.receipt();e={'ai_verification':r,'quote_verified':False}
        self.assertTrue(review_state.evidence_accepted(e,'current'))
        self.assertFalse(review_state.evidence_accepted(e,'changed'))
        r['valid_until']='2000-01-01T00:00:00+00:00'
        self.assertFalse(review_state.evidence_accepted(e,'current'))

    def test_expired_ai_audit_reopens_even_after_human_skip(self):
        import review_state
        r=self.receipt();r['valid_until']='2000-01-01T00:00:00+00:00'
        a={'audit_id':'x','kp_id':'k','type':'quote_missing','target':'e','priority':'P0','revision':'current','status':'done','ai_review':r,'human_review':{'verdict':'skip'}}
        self.assertEqual(review_state.kp_status('k',[a]),'pending')
        fresh={key:value for key,value in a.items() if key not in {'status','ai_review','human_review'}}
        self.assertEqual(review_state.reconcile([fresh],[a],{'k'})[0]['status'],'pending')
        a['human_review']={'verdict':'bad'};self.assertEqual(review_state.kp_status('k',[a]),'rejected')

    def test_expiry_blocks_content_even_if_original_quote_matches(self):
        import review_state
        r=self.receipt();r['valid_until']='2000-01-01T00:00:00+00:00'
        k={'kp_id':'k','verification_method':'ai_web','ai_verification':r}
        self.assertFalse(review_state.content_decision_accepted(k,[],'current'))
        a={'kp_id':'k','priority':'P0','revision':'current','human_review':{'verdict':'ok'}}
        self.assertTrue(review_state.content_decision_accepted(k,[a],'current'))

    def test_http402_opens_circuit_and_does_not_retry(self):
        import deepseek_web as web
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(kb,'LOG_DIR',Path(tmp)),mock.patch.object(web.llm,'load_env',return_value={'DEEPSEEK_BASE_URL':'https://api.deepseek.com','DEEPSEEK_API_KEY':'test-only'}),mock.patch.object(web.egress,'require_recipient'),mock.patch.object(web,'build_opener') as opener:
            web._blocked.clear();self.addCleanup(web._blocked.clear)
            opener.return_value.open.side_effect=HTTPError('https://api.deepseek.com/v1/tools/search',402,'Balance',{},None)
            with self.assertRaises(web.WebUnavailable):web.search('first')
            with self.assertRaises(web.WebUnavailable):web.search('second')
            self.assertEqual(opener.return_value.open.call_count,1)

    def test_pro_model_is_rejected_before_any_network_request(self):
        import ai_review
        with mock.patch.object(ai_review.deepseek_web,'search') as search:
            with self.assertRaises(ValueError):ai_review.review_one({},[],'',('deepseek-v4-pro','deepseek-flash'))
            search.assert_not_called()

    def test_budget_reserves_concurrent_calls_before_spending(self):
        from review_budget import ReviewBudget,BudgetExceeded
        budget=ReviewBudget(0.10)
        a=budget.reserve(0.06)
        with self.assertRaises(BudgetExceeded):budget.reserve(0.06)
        budget.settle(a,0.01)
        b=budget.reserve(0.06);budget.settle(b)
        self.assertAlmostEqual(budget.snapshot()['estimated_spent_usd'],0.07)
        self.assertEqual(budget.snapshot()['requests'],2)
        for invalid in [0,-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):ReviewBudget(invalid)

    def test_budget_shortfall_prevents_web_request(self):
        import deepseek_web as web
        from review_budget import ReviewBudget,BudgetExceeded
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(kb,'LOG_DIR',Path(tmp)),mock.patch.object(web.llm,'load_env',return_value={'DEEPSEEK_BASE_URL':'https://api.deepseek.com','DEEPSEEK_API_KEY':'test-only'}),mock.patch.object(web.egress,'require_recipient'),mock.patch.object(web,'build_opener') as opener:
            web._blocked.clear()
            with self.assertRaises(BudgetExceeded):web.search('unique query',ReviewBudget(0.00001))
            opener.return_value.open.assert_not_called()

    def test_auth_failure_stops_bounded_queue_and_models_stay_flash(self):
        import ai_review
        points=[{'kp_id':str(i),'course':'课程甲','name':'概念','definition':'完整定义','numbers':[],'status':'pending'} for i in range(20)]
        def load(path):return points if path==kb.KP_FILE else []
        with mock.patch.object(sys,'argv',['ai-review','--workers','2','--limit','20']),mock.patch.object(kb,'load_jsonl',side_effect=load),mock.patch.object(kb,'rewrite_jsonl'),mock.patch.object(kb,'write_json'),mock.patch.object(ai_review,'build_page_index',return_value={}),mock.patch.object(ai_review,'source_text_of',return_value='本地正文'),mock.patch.object(ai_review,'apply_results'),mock.patch.object(ai_review,'write_reports',return_value={}),mock.patch.object(ai_review,'run_local'),mock.patch.object(ai_review,'review_one',side_effect=ai_review.deepseek_web.WebUnavailable('HTTP 402')) as review:
            self.assertEqual(ai_review.main(),4)
            self.assertLessEqual(review.call_count,2)
            self.assertTrue(all(call.args[3]==('deepseek-flash','deepseek-flash') for call in review.call_args_list))


if __name__=='__main__':unittest.main()
