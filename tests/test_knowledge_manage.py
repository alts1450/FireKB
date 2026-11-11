import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class KnowledgeManagementTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
  self.env={**os.environ,'FIREKB_ROOT':str(self.root),'PYTHONUTF8':'1'}
  self.write('00_配置/courses.json',{'courses':[{'id':'T','name':'测试课'}]})
  self.rows=[{'kp_id':i,'course':'测试课','name':i+'标题','definition':'有效容积由流量和持续时间决定。','note':'','numbers':[],'importance':3,'status':'verified','source_file':'a.pdf','page_label_start':'第1页','page_label_end':'第1页'} for i in ['a','b','c']]
  self.jsonl('30_知识点/kp.jsonl',self.rows)
  for f in ['evidence','audit','ai_reviews','quality']:
   self.jsonl('30_知识点/'+f+'.jsonl',[{'kp_id':i,'evidence_id':i+'-E','quote':'有效容积由流量和持续时间决定。','quote_verified':True,'source_file':'a.pdf','page_label':'第1页'} for i in ['a','b','c']])
  self.jsonl('20_文本库/pages.jsonl',[{'course':'测试课','source_file':'a.pdf','page_no':1,'page_label':'第1页','text':'有效容积由流量和持续时间决定。'}])
 def write(self,name,value):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
 def jsonl(self,name,rows):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
 def read(self,name):return [json.loads(s) for s in (self.root/name).read_text(encoding='utf-8').split('\n') if s]
 def call(self,data,code=0):
  p=subprocess.run([sys.executable,'-B','tools/knowledge_manage.py'],input=json.dumps(data),cwd=ROOT,env=self.env,capture_output=True,text=True,encoding='utf-8');self.assertEqual(p.returncode,code,p.stderr);return json.loads(p.stdout) if code==0 else p.stderr
 def test_edit_backup_and_stale_revision(self):
  d=self.call({'operation':'inspect','kp_id':'a'})
  result=self.call({'operation':'update','kp_id':'a','token':d['token'],'patch':{**d['point'],'definition':'更新后的有效容积计算说明。','note':'补充批注'}})
  a=self.read('30_知识点/kp.jsonl')[0];self.assertEqual(a['status'],'pending');self.assertEqual(a['note'],'补充批注')
  original=Path(result['backup'])/'files/30_知识点/kp.jsonl';self.assertEqual(json.loads(original.read_text(encoding='utf-8').split('\n')[0])['status'],'verified')
  self.call({'operation':'update','kp_id':'a','token':d['token'],'patch':{'name':'并发修改'}},code=2)
  p=subprocess.run([sys.executable,'-B','-m','firekb','verify','--course','T'],cwd=ROOT,env=self.env,capture_output=True,text=True,encoding='utf-8');self.assertEqual(p.returncode,0,p.stderr)
  self.assertEqual(self.read('30_知识点/kp.jsonl')[0]['status'],'pending')
  p=subprocess.run([sys.executable,'-B','-c',"import sys;sys.path.insert(0,'tools');import human_review as h,kb;\nwith kb.writer_lock():h.approve('a',h.inspect('a')['token'])"],cwd=ROOT,env=self.env,capture_output=True,text=True,encoding='utf-8');self.assertEqual(p.returncode,0,p.stderr)
  self.assertEqual(self.read('30_知识点/kp.jsonl')[0]['status'],'verified')
 def test_batch_delete_removes_linked_records_and_keeps_other_points(self):
  d=self.call({'operation':'prepare_delete','ids':['a','b']})
  r=self.call({'operation':'delete','ids':d['ids'],'delete_token':d['delete_token']})
  for f in ['kp','evidence','audit','ai_reviews','quality']:self.assertEqual([k['kp_id'] for k in self.read('30_知识点/'+f+'.jsonl')],['c'])
  self.assertTrue((Path(r['backup'])/'manifest.json').exists());self.assertEqual(len(self.read('20_文本库/pages.jsonl')),1)
 def test_delete_changed_selection_is_atomic(self):
  d=self.call({'operation':'prepare_delete','ids':['a','b']});self.rows[0]['note']='外部更新';self.jsonl('30_知识点/kp.jsonl',self.rows)
  self.call({'operation':'delete','ids':d['ids'],'delete_token':d['delete_token']},code=2)
  self.assertEqual(len(self.read('30_知识点/kp.jsonl')),3)
 def test_invalid_fields_write_nothing(self):
  d=self.call({'operation':'inspect','kp_id':'a'})
  for patch in [{'name':''},{'importance':9},{'status':'verified'},{'definition':3}]:self.call({'operation':'update','kp_id':'a','token':d['token'],'patch':patch},code=2)
  self.assertEqual(self.read('30_知识点/kp.jsonl'),self.rows)

if __name__=='__main__':unittest.main()
