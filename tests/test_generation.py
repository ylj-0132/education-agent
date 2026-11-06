import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from profiling.api_client import CachedChatClient
from profiling.run_store import RunStore, RecordedTransport
from recommendation.context import build_generation_request
from test_recommendation_comparison import context, response
from generation.workflow import DIMENSIONS, validate_question, validate_review, run_generation, load_recommendation, model_input


def request():
    return build_generation_request(context(), selected_concept_ids=['a'], reason='练习数组')


def question():
    return {'outcome': 'question', 'reason': '可以覆盖目标', 'title': '数组求和',
        'statement': '读取两个整数并求和。使用数组保存两个值。', 'input_format': '两个整数',
        'output_format': '输出总和', 'constraints': '每个整数0至10',
        'samples': [{'input': '1 2', 'output': '3', 'explanation': '1+2=3'}],
        'reference_code': 'public class Main { public static void main(String[] args) {} }',
        'explanation': '保存后遍历求和', 'coverage': [{'concept_id': 'a', 'location': '使用数组保存输入'}]}


def review(q):
    return {'question_sha256': __import__('profiling.run_store', fromlist=['fingerprint']).fingerprint(q),
        'checks': [{'dimension': d, 'status': 'not_applicable' if d == 'combination' else 'pass',
                    'reason': '已检查' if d != 'combination' else '单目标', 'location': '题干'} for d in DIMENSIONS]}


class GenerationTests(unittest.TestCase):
    def test_no_repeat_policy_applies_to_generation_revision_and_review(self):
        from recommendation.comparison import COMMON
        req=request(); q=question()
        for messages in [model_input(req),model_input(req,feedback={'previous_question':q,'review':review(q)}),model_input(req,q=q)]:
            rules=messages[0]['content']
            self.assertIn('禁止复用原题',rules)
            self.assertIn('仅替换名词、故事背景、变量名或数值',rules)
            self.assertNotIn('确需复习或复用时',rules)
            self.assertIn('同一知识点可以继续练习',rules)
        self.assertIn('student_fit应判为fail',model_input(req,q=q)[0]['content'])
        self.assertIn('不得安排原题',COMMON)
        self.assertNotIn('避免无理由重复',COMMON)

    def test_prompt_schema_contains_exact_targets_and_keeps_strict_validation(self):
        req=request(); req['target_concept_ids']=['a','b']
        rules=model_input(req)[0]['content']
        schema=json.loads(next(line.split('正常题格式：',1)[1] for line in rules.splitlines() if line.startswith('正常题格式：')))
        self.assertEqual([c['concept_id'] for c in schema['coverage']],['a','b'])
        q=question(); q['coverage'].append({'concept_id':'b','location':'extra'})
        with self.assertRaises(ValueError):validate_question(request(),q)

    def test_prompt_compacts_uncited_raw_evidence_without_losing_profile_or_sources(self):
        import copy
        req=request(); req['target_source']='model_direct'; req['reason_evidence_ids']=['r1']
        req['evidence']=[{'record_id':r,'question':'question '+r,'program':'program '+r,'source':{'sha256':r}} for r in ['r1','r2']]
        original=copy.deepcopy(req)
        projected=json.loads(model_input(req)[1]['content'])['request']
        self.assertEqual(projected['evidence'][0],req['evidence'][0])
        self.assertNotIn('program',projected['evidence'][1])
        self.assertNotIn('question',projected['evidence'][1])
        self.assertEqual(projected['evidence'][1]['source'],req['evidence'][1]['source'])
        self.assertEqual(projected['concept_context'],req['concept_context'])
        self.assertEqual(req,original)
        req['target_source']='explicit_selection'
        self.assertEqual(json.loads(model_input(req)[1]['content'])['request']['evidence'],req['evidence'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def client(self, store, transport):
        return CachedChatClient(base_url='https://example.com/v1', api_key='fake', model='test',
            cache_dir=self.root/'cache', max_calls=8, transport=RecordedTransport(store, transport), event_sink=store.api_event)

    def test_content_contract_and_review_version(self):
        q = question(); validate_question(request(), q); validate_review(request(), q, review(q))
        for bad in [dict(q, coverage=[]), dict(q, reference_code=''), dict(q, samples=[])]:
            with self.assertRaises(ValueError): validate_question(request(), bad)
        r = review(q); r['question_sha256'] = 'wrong'
        with self.assertRaises(ValueError): validate_review(request(), q, r)
        r = review(q); r['checks'][0]['status'] = 'not_applicable'
        with self.assertRaises(ValueError): validate_review(request(), q, r)

    def test_all_model_pass_still_needs_execution_and_resume_no_calls(self):
        q = question(); outputs = iter([q, review(q)])
        with RunStore(self.root/'run', {'test': 1}, 8) as store:
            result = run_generation(store, self.client(store, lambda *a: response(next(outputs))), request())
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(result['execution_check']['status'], 'not_run')
            self.assertEqual(len(store.state['attempts']), 2)
        for p in (self.root/'cache').glob('*.json'): p.unlink()
        with RunStore(self.root/'run', {'test': 1}, 8, resume=True) as store:
            resumed = run_generation(store, self.client(store, lambda *a: self.fail('network')), request())
        self.assertEqual(resumed['question_id'], result['question_id'])
        self.assertEqual(len(resumed['versions']), 1)

    def test_revision_once_and_checks_bound_to_new_version(self):
        q = question(); bad = review(q); bad['checks'][0]['status'] = 'fail'
        q2 = dict(q, statement='明确后的新题干'); bad2 = review(q2); bad2['checks'][0]['status'] = 'fail'
        outputs = iter([q, bad, q2, bad2])
        with RunStore(self.root/'run', {'test': 1}, 8) as store:
            result = run_generation(store, self.client(store, lambda *a: response(next(outputs))), request())
            self.assertEqual(len(store.state['attempts']), 4)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(len(result['versions']), 2)
        self.assertNotEqual(result['versions'][0]['question_sha256'], result['versions'][1]['question_sha256'])

    def test_split_request_does_not_call_reviewer(self):
        with RunStore(self.root/'run', {'test': 1}, 8) as store:
            r = run_generation(store, self.client(store, lambda *a: response({'outcome': 'needs_split', 'reason': '需拆题'})), request())
            self.assertEqual(len(store.state['attempts']), 1)
        self.assertEqual(r['status'], 'needs_split')

    def test_output_failure_is_traced_and_checkpoint_can_rebuild(self):
        from generation import workflow
        real_write = workflow.atomic_json
        def broken(path, value):
            if path.name == 'generation.json': raise OSError('disk failure')
            real_write(path, value)
        outputs = iter([question(), review(question())])
        with RunStore(self.root/'run', {'test': 1}, 8) as store:
            with patch.object(workflow, 'atomic_json', broken), self.assertRaises(OSError):
                run_generation(store, self.client(store, lambda *a: response(next(outputs))), request())
            self.assertEqual(store.state['status'], 'interrupted')
            self.assertTrue(any(e['event'] == 'generation_output_failed' for e in store.snapshot()['events']))
        with RunStore(self.root/'run', {'test': 1}, 8, resume=True) as store:
            r = run_generation(store, self.client(store, lambda *a: self.fail('network')), request())
        self.assertEqual(r['status'], 'needs_review')

    @patch('time.sleep')
    def test_invalid_response_failure_is_not_quality_rejection(self, sleep):
        with RunStore(self.root/'run', {'test': 1}, 8) as store:
            r = run_generation(store, self.client(store, lambda *a: response({'bad': 1})), request())
            self.assertEqual(len(store.state['attempts']), 2)
        self.assertEqual(r['status'], 'failed')
        with RunStore(self.root/'run', {'test': 1}, 8, resume=True) as store:
            r = run_generation(store, self.client(store, lambda *a: self.fail('network')), request())
            self.assertEqual(len(store.state['attempts']), 2)

    def test_interrupt_preserves_unknown_and_does_not_resend(self):
        def interrupt(*args): raise KeyboardInterrupt()
        with RunStore(self.root/'run', {'test': 1}, 8) as store:
            with self.assertRaises(KeyboardInterrupt): run_generation(store, self.client(store, interrupt), request())
        with RunStore(self.root/'run', {'test': 1}, 8, resume=True) as store:
            r = run_generation(store, self.client(store, lambda *a: self.fail('network')), request())
            self.assertEqual(store.state['attempts'][0]['status'], 'unknown')
        self.assertEqual(r['status'], 'failed')

    def test_recommendation_loader_rejects_incomplete_and_modified_handoff(self):
        from recommendation.runner import run_recommendation
        from test_recommendation_comparison import decision
        with RunStore(self.root/'rec', {'test': 1}, 2) as store:
            report = run_recommendation(store, self.client(store, lambda *a: response(decision())), context())
        self.assertEqual(load_recommendation(self.root/'rec')['recommendation_id'], report['recommendation_id'])
        report['generation_request']['target_concept_ids'] = ['b']
        (self.root/'rec/recommendation.json').write_text(json.dumps(report), encoding='utf-8')
        with self.assertRaises(ValueError): load_recommendation(self.root/'rec')

    def test_teacher_cli_preview_execute_resume_and_status(self):
        from test_recommendation import catalog
        (self.root/'catalog.json').write_text(json.dumps(catalog()), encoding='utf-8')
        args = ['--catalog', str(self.root/'catalog.json'), '--student', 's1', '--scope', 'a',
            '--targets', 'a', '--requirement', '练习数组', '--output', str(self.root/'cli'),
            '--cache', str(self.root/'cli-cache'), '--env-file', str(self.root/'test.env')]
        preview = subprocess.run([sys.executable, 'generate_question.py', *args], capture_output=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse((self.root/'cli').exists())
        (self.root/'test.env').write_text('ZZZ_BASE_URL=https://example.com/v1\nZZZ_API_KEY=fake\nZZZ_MODEL=test\nZZZ_MAX_TOKENS=1500\n', encoding='utf-8')
        bootstrap = '''import runpy,json
import profiling.api_client as api
values=iter(VALUES)
def fake(*args):return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(next(values))}}]}
api.send_chat=fake
runpy.run_path('generate_question.py',run_name='__main__')
'''.replace('VALUES', repr([question(), review(question())]))
        for flags in [[], ['--resume']]:
            r = subprocess.run([sys.executable, '-c', bootstrap, *args, '--execute', *flags], capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(RunStore.inspect(self.root/'cli')['requests_reserved'], 2)
        self.assertEqual(RunStore.inspect(self.root/'cli',full=True)['identity']['max_tokens'],6000)
        r = subprocess.run([sys.executable, '-c', bootstrap, *args, '--output', str(self.root/'cli-explicit'),
                            '--max-output-tokens', '4000', '--execute'], capture_output=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(RunStore.inspect(self.root/'cli-explicit',full=True)['identity']['max_tokens'],4000)
        r = subprocess.run([sys.executable, '-c', bootstrap, *args, '--max-output-tokens', '4000',
                            '--execute', '--resume'], capture_output=True)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(RunStore.inspect(self.root/'cli')['requests_reserved'],2)
        (self.root/'test.env').unlink()
        r = subprocess.run([sys.executable, 'generate_question.py', '--trace', '--output', str(self.root/'cli')], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
