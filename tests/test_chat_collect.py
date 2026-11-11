import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class CollectionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.env={**os.environ,'FIREKB_ROOT':str(self.root),'PYTHONUTF8':'1'}
  self.write('00_配置/courses.json',{'courses':[{'id':'TEST','name':'测试课'}]})
  self.aid='a'*32
  self.write('90_日志/chat-answers/'+self.aid+'.json',{'answer':{'model':'deepseek-flash','segments':[{'text':'流量为20升每秒，持续两小时，所需储水量为144立方米。','kind':'unsupported'},{'text':'另一段不要收录','kind':'normal'}],'web_sources':[]},'messages':[{'role':'user','content':'帮我算一下储水量'}]})
  self.data={'answer_id':self.aid,'indices':[0],'title':'储水量计算','course_id':'TEST','note':'老师批注：注意单位换算','confirmed':True}
 def write(self,name,value):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
 def read_rows(self,name):return [json.loads(x) for x in (self.root/name).read_text(encoding='utf-8').split('\n') if x]
 def call(self,data=None,code=0):
  p=subprocess.run([sys.executable,'-B','tools/chat_collect.py'],input=json.dumps(data or self.data),cwd=ROOT,env=self.env,capture_output=True,text=True,encoding='utf-8');self.assertEqual(p.returncode,code,p.stdout+p.stderr);return json.loads(p.stdout) if code==0 else p.stderr
 def stage(self,*args):
  p=subprocess.run([sys.executable,'-B','-m','firekb',*args],cwd=ROOT,env=self.env,capture_output=True,text=True,encoding='utf-8');self.assertEqual(p.returncode,0,p.stdout+p.stderr)
 def test_collect_verify_export_and_retrieve(self):
  result=self.call();kp=self.read_rows('30_知识点/kp.jsonl')[0]
  self.assertEqual(kp['source_label'],'网络');self.assertEqual(kp['verification_method'],'human');self.assertEqual(kp['status'],'verified');self.assertEqual(kp['note'],self.data['note'])
  self.assertNotIn('另一段',kp['definition']);self.assertEqual(kp['source_urls'],[])
  self.assertEqual(self.call()['duplicate'],True);self.assertEqual(len(self.read_rows('30_知识点/kp.jsonl')),1)
  self.stage('verify','--course','TEST','--recheck');self.assertEqual(self.read_rows('30_知识点/kp.jsonl')[0]['status'],'verified')
  self.stage('review','--course','TEST');self.stage('export','--course','TEST')
  cards=(self.root/'40_复习产物/TEST/02_背诵卡.md').read_text(encoding='utf-8');self.assertIn('人工核验',cards);self.assertIn('网络',cards)
  p=subprocess.run([sys.executable,'-B','-c',"import sys;sys.path.insert(0,'tools');import kb_chat;assert len(kb_chat.retrieve('储水量计算'))==1"],cwd=ROOT,env=self.env,capture_output=True,text=True,encoding='utf-8');self.assertEqual(p.returncode,0,p.stderr)
 def test_invalid_selection_confirmation_course_and_path_do_not_write(self):
  for patch in [{'indices':[]},{'indices':[99]},{'confirmed':False},{'course_id':'missing'},{'answer_id':'../x'}]:
   self.call({**self.data,**patch},code=2);self.assertFalse((self.root/'30_知识点/kp.jsonl').exists())
 def test_modified_content_requires_new_human_review(self):
  self.call();rows=self.read_rows('30_知识点/kp.jsonl');rows[0]['definition']='修改后的内容'
  (self.root/'30_知识点/kp.jsonl').write_text(json.dumps(rows[0],ensure_ascii=False)+'\n',encoding='utf-8')
  self.stage('verify','--course','TEST');self.assertEqual(self.read_rows('30_知识点/kp.jsonl')[0]['status'],'pending')
 def test_human_rejection_survives_verify(self):
  self.call();rows=self.read_rows('30_知识点/audit.jsonl');rows[0]['human_review']['verdict']='bad'
  (self.root/'30_知识点/audit.jsonl').write_text(json.dumps(rows[0],ensure_ascii=False)+'\n',encoding='utf-8')
  self.stage('verify','--course','TEST');self.assertEqual(self.read_rows('30_知识点/kp.jsonl')[0]['status'],'rejected')
 def test_collect_arbitrary_ranges_preserves_only_selected_original(self):
  ranges=[{'index':0,'start':3,'end':9},{'index':1,'start':0,'end':3}]
  data={**self.data,'ranges':ranges,'definition':'我整理后的知识点正文'}
  result=self.call(data);kp=self.read_rows('30_知识点/kp.jsonl')[0];ev=self.read_rows('30_知识点/evidence.jsonl')[0]
  self.assertEqual(kp['definition'],'我整理后的知识点正文');self.assertEqual(kp['selected_ranges'],ranges)
  self.assertEqual(ev['quote'],'20升每秒，\n\n另一段')
  self.assertTrue(self.call(data)['duplicate'])
  self.stage('verify','--course','TEST');self.assertEqual(self.read_rows('30_知识点/kp.jsonl')[0]['status'],'verified')
 def test_invalid_text_ranges_do_not_write(self):
  for ranges in [[],[{'index':0,'start':-1,'end':4}],[{'index':0,'start':3,'end':3}],[{'index':0,'start':0,'end':999}],[{'index':99,'start':0,'end':1}]]:
   self.call({**self.data,'ranges':ranges},code=2);self.assertFalse((self.root/'30_知识点/kp.jsonl').exists())
if __name__=='__main__':unittest.main()
