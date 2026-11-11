import sys,unittest,json,tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import kb_chat as chat
import llm,kb

class ChatTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        logs=mock.patch.object(kb,'LOG_DIR',Path(temp.name));logs.start();self.addCleanup(logs.stop)
        consent=mock.patch.object(llm.eg,'require_recipient');self.consent=consent.start();self.addCleanup(consent.stop)

    def test_chat_requires_approval_for_questions_before_network_calls(self):
        self.consent.side_effect=PermissionError('请批准对话数据')
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat.deepseek_web,'search') as search,mock.patch.object(llm,'chat') as model:
            with self.assertRaises(PermissionError):chat.answer({'messages':[{'role':'user','content':'个人笔记中的问题'}],'web':True})
            search.assert_not_called();model.assert_not_called()
        self.assertEqual(self.consent.call_args.args[1],'personal')

    def test_citations_are_checked_not_trusted(self):
        sources=[{'id':'KP1','original':'消防水池用于储存消防用水。','name':'消防水池','source':'a.pdf','page':'1'}]
        parts=chat.validate_answer({'segments':[{'text':'根据知识库：\n'},{'text':'消防水池用于储存消防用水。','source_id':'KP1'},{'text':'水池最小容积为999立方米。','source_id':'KP1'},{'text':'消防水池用于储存消防用水。','source_id':'fake'},{'text':'模型编写事实','kind':'normal'}]},sources)
        self.assertEqual([p['kind'] for p in parts],['normal','cited','unsupported','unsupported','unsupported'])
        self.assertEqual(parts[1]['citation']['original'],sources[0]['original'])
    def test_no_sources_stays_yellow(self):
        self.assertEqual(chat.validate_answer({'segments':[{'text':'没有相关资料','source_id':'fake'}]},[])[0]['kind'],'unsupported')
        with self.assertRaises(ValueError):chat.validate_answer({'segments':[]},[])
    def test_supported_explanation_and_calculation_are_green(self):
        sources=[{'id':'KP1','original':'有效容积V等于流量Q与时间t的乘积。'}]
        parts=chat.validate_answer({'segments':[
            {'text':'按20升每秒持续2小时计算，容积为144立方米。','basis':'calculation','source_id':'KP1','support_quote':'有效容积V等于流量Q与时间t的乘积。'},
            {'text':'也就是储水量要覆盖整个用水时段。','basis':'knowledge','source_id':'KP1','support_quote':'流量Q与时间t的乘积'},
            {'text':'外部知识补充','basis':'general'},
            {'text':'伪造依据的解释','basis':'knowledge','source_id':'KP1','support_quote':'从未出现的原文'}]},sources)
        self.assertEqual([p['kind'] for p in parts],['cited','cited','unsupported','unsupported'])
    def test_calculation_without_library_is_answered_as_external_knowledge(self):
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=[]),mock.patch.object(chat.deepseek_web,'search') as search,mock.patch.object(llm,'chat',return_value={'content':'{"segments":[{"text":"12乘25等于300。","basis":"calculation"}]}','model':'deepseek-flash'}) as call:
            result=chat.answer({'messages':[{'role':'user','content':'12乘25是多少？'}]})
            self.assertIn('300',result['segments'][0]['text']);self.assertEqual(result['segments'][0]['kind'],'unsupported')
            self.assertIn('直接完成计算，列出公式、代入和结果',call.call_args.args[0][0]['content']);search.assert_not_called()
    def test_calculation_can_continue_preceding_library_formula(self):
        sources=[{'id':'KP1','original':'容积等于流量与时间的乘积。'}]
        parts=chat.validate_answer({'segments':[
            {'text':'容积等于流量与时间的乘积。','source_id':'KP1'},
            {'text':'代入得144立方米。','basis':'calculation'},
            {'text':'外部知识','basis':'general'},
            {'text':'外部推算','basis':'calculation'}]},sources)
        self.assertEqual([p['kind'] for p in parts],['cited','cited','unsupported','unsupported'])
    def test_search_failure_still_answers(self):
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=[]),mock.patch.object(chat.deepseek_web,'search',side_effect=RuntimeError('network unavailable')),mock.patch.object(llm,'chat',return_value={'content':'{"segments":[{"text":"使用自身知识回答。","basis":"general"}]}','model':'deepseek-flash'}):
            result=chat.answer({'messages':[{'role':'user','content':'问题'}],'web':True})
            self.assertEqual(result['web_sources'],[]);self.assertTrue(result['web_error']);self.assertEqual(result['segments'][0]['kind'],'unsupported')
    def test_pro_rejected_before_call(self):
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none','low','high','max']}),mock.patch.object(llm,'chat') as call:
            with self.assertRaises(ValueError):chat.answer({'model':'deepseek-v4-pro'})
            call.assert_not_called()
    def test_answer_passes_selected_effort_and_bounds_history(self):
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none','low','high','max']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=[]),mock.patch.object(llm,'chat',return_value={'content':'{"segments":[{"text":"无原文依据"}]}','model':'deepseek-flash'}) as call:
            r=chat.answer({'messages':[{'role':'user','content':'问题'}]*15,'effort':'low'})
            self.assertEqual(len(call.call_args.args[0]),11)
            self.assertEqual(call.call_args.kwargs['reasoning_effort'],'low')
            self.assertEqual(call.call_args.kwargs['retries'],1)
            self.assertEqual(r['segments'][0]['kind'],'unsupported')
    def test_retrieval_excludes_pending_and_stale_human_decisions(self):
        rows=[{'kp_id':'a','name':'消防水池','definition':'消防储水','status':'verified'}, {'kp_id':'b','name':'消防水池','status':'pending'}, {'kp_id':'c','name':'消防水池','status':'verified','verification_method':'human','human_review':{'revision':'old','verdict':'ok'}}]
        es=[{'kp_id':k,'quote':'消防水池原文','quote_verified':True} for k in ['a','b','c']]
        with mock.patch.object(kb,'load_jsonl',side_effect=lambda p:rows if p==kb.KP_FILE else es if p==kb.EVIDENCE_FILE else []),mock.patch.object(kb,'is_experimental_kp',return_value=False),mock.patch.object(chat,'build_page_index',return_value={}),mock.patch.object(chat,'source_text_of',return_value='消防水池原文'):
            self.assertEqual([r['id'] for r in chat.retrieve('消防水池')],['a'])
    def test_reasoning_payload(self):
        response=mock.MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]}).encode()
        cfg={'DEEPSEEK_BASE_URL':'https://api.deepseek.com','DEEPSEEK_API_KEY':'test-valid-key'}
        with mock.patch.object(llm,'load_env',return_value=cfg),mock.patch.object(llm.eg,'require_recipient'),mock.patch.object(llm.urllib.request,'build_opener') as opener:
            opener.return_value.open.return_value=response
            llm.chat([{'role':'user','content':'test'}],reasoning_effort='high',retries=1)
            body=json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertEqual(body['thinking'],{'type':'enabled'});self.assertEqual(body['reasoning_effort'],'high')
            llm.chat([{'role':'user','content':'test'}],reasoning_effort='none',retries=1)
            body=json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertEqual(body['thinking'],{'type':'disabled'});self.assertNotIn('reasoning_effort',body)

    def test_truncated_answer_keeps_complete_passages_without_retry(self):
        partial='{"segments":[{"text":"第1题：答案D。","basis":"general"},{"text":"第2题：未完'
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=[]),mock.patch.object(llm,'chat',return_value={'content':partial,'finish_reason':'length'}) as call:
            result=chat.answer({'messages':[{'role':'user','content':'请逐题回答。'*600}]})
        self.assertEqual([s['text'] for s in result['segments']],['第1题：答案D。'])
        self.assertTrue(result['incomplete']);self.assertIn('上限',result['warning'])
        self.assertEqual(call.call_count,1);self.assertEqual(call.call_args.kwargs['retries'],1)
        self.assertGreater(call.call_args.kwargs['max_tokens'],5000)
        saved=kb.load_json(kb.LOG_DIR/'chat-answers'/(result['answer_id']+'.json'),{})
        self.assertEqual(saved['response']['content'],partial)

    def test_partial_json_does_not_invent_or_trust_citations(self):
        first={'text':'引号"、括号 } ] 和公式 \\frac{1}{2}。','source_id':'fake'}
        raw,warning=chat.parse_response({'content':'```json\n{"segments":['+json.dumps(first,ensure_ascii=False)+',{"text":"broken\\q"}]}','finish_reason':'stop'})
        parts=chat.validate_answer(raw,[])
        self.assertEqual(parts[0]['text'],first['text']);self.assertEqual(parts[0]['kind'],'unsupported')
        self.assertEqual(len(parts),1);self.assertIn('格式不完整',warning)

    def test_complete_long_answer_is_not_silently_cut(self):
        rows=[{'text':f'第{i}题答案。','basis':'general'} for i in range(1,121)]
        rows.append({'text':'完整长段落。'*2500,'basis':'general'})
        raw,warning=chat.parse_response({'content':json.dumps({'segments':rows}),'finish_reason':'stop'})
        parts=chat.validate_answer(raw,[])
        self.assertEqual(len(parts),121);self.assertEqual(parts[-1]['text'],rows[-1]['text']);self.assertFalse(warning)
        _,warning=chat.parse_response({'content':json.dumps({'segments':rows}),'finish_reason':'length'})
        self.assertIn('上限',warning)

    def test_unrecoverable_answer_has_diagnostic_not_raw_json_error(self):
        response={'content':'{"segments":[{"text":"未完','finish_reason':'length','usage':{'completion_tokens':5000}}
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=[]),mock.patch.object(llm,'chat',return_value=response):
            with self.assertRaisesRegex(ValueError,'输出上限.*本地诊断记录'):
                chat.answer({'messages':[{'role':'user','content':'题目'}]})
        logs=list((kb.LOG_DIR/'chat-errors').glob('*.json'));self.assertEqual(len(logs),1)
        self.assertEqual(kb.load_json(logs[0],{})['response'],response)

    def test_followup_accepts_a_long_previous_answer(self):
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=[]),mock.patch.object(llm,'chat',return_value={'content':'{"segments":[{"text":"继续回答。"}]}','finish_reason':'stop'}) as call:
            result=chat.answer({'messages':[{'role':'user','content':'问题'},{'role':'assistant','content':'前面的长答案。'*2000},{'role':'user','content':'继续回答剩余部分'}]})
        self.assertEqual(call.call_args.args[0][2]['content'],'前面的长答案。'*2000)
        self.assertFalse(result['incomplete'])

    def test_ten_short_questions_reserve_reasoning_and_detect_missing_answers(self):
        question='\n'.join(f'{i}. 请选择正确答案\nA 甲 B 乙 C 丙 D 丁' for i in range(1,11))
        response={'content':json.dumps({'segments':[{'text':f'{i}. 答案：D。','basis':'general','question_id':i} for i in [1,2]]}),
                  'finish_reason':'stop','usage':{'completion_tokens_details':{'reasoning_tokens':4864}}}
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none','low','high','max']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=[]),mock.patch.object(llm,'chat',return_value=response) as call:
            result=chat.answer({'messages':[{'role':'user','content':question}],'effort':'high'})
        self.assertGreaterEqual(call.call_args.kwargs['max_tokens'],24000)
        self.assertEqual(call.call_args.kwargs['reasoning_effort'],'high')
        self.assertEqual(call.call_count,1)
        self.assertEqual(result['question_coverage']['missing'],list(range(3,11)))
        self.assertTrue(result['incomplete']);self.assertIn('10',result['warning'])
        self.assertIn('question_id',call.call_args.args[0][0]['content'])

    def test_choice_numbering_accepts_compact_options_and_checks_all_ten(self):
        question='\n'.join(f'{i}. 选择题\nA 10s B 30s C60s D120s' for i in range(1,11))
        expected=chat.choice_question_ids(question)
        self.assertEqual(expected,list(range(1,11)))
        complete={'segments':[{'text':f'第{i}题：D。解析。'} for i in range(1,11)]}
        self.assertEqual(chat.question_coverage(complete,expected)['missing'],[])
        self.assertEqual(chat.choice_question_ids('1. 建议甲\n2. 建议乙'),[])
        self.assertLess(chat.output_budget(question,'none',10),chat.output_budget(question,'high',10))

    def test_pdf_line_wrapping_does_not_discard_a_real_knowledge_anchor(self):
        source={'id':'K','original':'Measure pulse A and\npulse B at 4.4 μm; a signiﬁcant difference signals drift.'}
        parts=chat.validate_answer({'segments':[{'text':'比较红外辐射强度可用于识别火焰。','basis':'knowledge','source_id':'K','support_quote':'Measure pulse A and pulse B at 4.4 μm; a significant difference signals drift.'},
            {'text':'伪造数值','basis':'knowledge','source_id':'K','support_quote':'pulse B at 5.4 μm'}]},[source])
        self.assertEqual([p['kind'] for p in parts],['cited','unsupported'])
        self.assertEqual(parts[0]['citation']['original'],source['original'])

    def test_current_question_does_not_inherit_unrelated_previous_search_terms(self):
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=[]) as search,mock.patch.object(llm,'chat',return_value={'content':'{"segments":[{"text":"答案"}]}'}):
            chat.answer({'messages':[{'role':'user','content':'解释消防水池容积与喷头工作温度'},{'role':'assistant','content':'前一题回答'},{'role':'user','content':'简述光电感烟探测器的工作原理'}]})
        self.assertEqual(search.call_args.args[0],'简述光电感烟探测器的工作原理')

    def test_partial_extraction_can_supply_traceable_quotes_without_promoting_batch(self):
        row={'kp_id':'sample','name':'光电感烟原理','definition':'烟雾散射光线。','status':'verified'}
        ev={'kp_id':'sample','quote':'烟雾散射光线。','quote_verified':True}
        batch={'id':'test-batch'}
        with mock.patch.object(kb,'load_jsonl',side_effect=lambda p:[row] if p==kb.KP_FILE else [ev] if p==kb.EVIDENCE_FILE else []),mock.patch.object(kb,'is_experimental_kp',return_value=batch),mock.patch.object(chat,'build_page_index',return_value={}),mock.patch.object(chat,'source_text_of',return_value='烟雾散射光线。'):
            found=chat.retrieve('光电感烟原理')
            self.assertEqual([s['id'] for s in found],['sample'])
            self.assertIn('局部抽取',found[0]['coverage_note'])
            ev['quote']='原文不存在的引文'
            self.assertEqual(chat.retrieve('光电感烟原理'),[])
        self.assertEqual(row['status'],'verified');self.assertNotIn('human_review',row)

    def test_real_usage_count_is_distinct_from_retrieved_count(self):
        sources=[{'id':'K1','name':'原理','original':'烟雾散射光线。'},{'id':'K2','name':'其他知识','original':'其他原文。'}]
        raw={'segments':[{'text':'根据散射信号变化报警。','basis':'knowledge','source_id':'K1','support_quote':'烟雾散射光线。'},
                         {'text':'外部资料补充。','basis':'general'}]}
        with mock.patch.object(chat,'options',return_value={'models':['deepseek-flash'],'defaultModel':'deepseek-flash','efforts':['none']}),mock.patch.object(llm,'has_valid_key',return_value=True),mock.patch.object(chat,'retrieve',return_value=sources),mock.patch.object(chat.deepseek_web,'search',return_value=[{'title':'网页','url':'https://example.org','content':'网页参考文字'}]),mock.patch.object(llm,'chat',return_value={'content':json.dumps(raw)}) as call:
            a=chat.answer({'messages':[{'role':'user','content':'说明原理'}],'web':True})
        self.assertEqual(a['source_count'],2);self.assertEqual(a['cited_source_count'],1)
        self.assertEqual([s['kind'] for s in a['segments']],['cited','unsupported'])
        self.assertIn('同一回答中知识库已有依据的句子与外部补充必须拆成不同segments',call.call_args.args[0][0]['content'])
        self.assertEqual(kb.load_json(kb.LOG_DIR/'chat-answers'/(a['answer_id']+'.json'),{})['sources'],sources)

    def test_short_followup_retains_context_but_new_topic_does_not(self):
        history=[{'role':'user','content':'水池的容积公式是什么？'},{'role':'assistant','content':'公式说明。'},{'role':'user','content':'那流量20升每秒持续两小时呢？'}]
        self.assertIn('水池的容积公式',chat.retrieval_query(history))
        history[-1]['content']='简述红外光束探测原理'
        self.assertEqual(chat.retrieval_query(history),'简述红外光束探测原理')

    def test_multi_question_retrieval_gives_each_topic_a_slot(self):
        rows=[{'kp_id':f'a{i}','name':'消防水池容积计算','definition':'消防水池容积计算。','status':'verified'} for i in range(15)]+[{'kp_id':'b','name':'红外光束探测原理','definition':'红外光束探测原理。','status':'verified'}]
        evidence=[{'kp_id':k['kp_id'],'quote':k['definition'],'quote_verified':True} for k in rows]
        with mock.patch.object(kb,'load_jsonl',side_effect=lambda p:rows if p==kb.KP_FILE else evidence if p==kb.EVIDENCE_FILE else []),mock.patch.object(kb,'is_experimental_kp',return_value=None),mock.patch.object(chat,'build_page_index',return_value={}),mock.patch.object(chat,'source_text_of',side_effect=lambda k,*_:k['definition']):
            found=chat.retrieve('1. 简述消防水池容积计算方法\n2. 简述红外光束探测原理')
        self.assertIn('b',[s['id'] for s in found[:2]])

if __name__=='__main__':unittest.main()
