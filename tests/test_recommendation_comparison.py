from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from profiling.api_client import CachedChatClient
from profiling.run_store import RunStore, RecordedTransport
from recommendation.context import prepare_context, build_generation_request
from recommendation.comparison import validate_candidates, validate_decision, run_comparison
from test_recommendation import catalog, profile


def context():
    return prepare_context(catalog(), student_id='s1', profile=profile(),
                           allowed_concept_ids=['a', 'b', 'c'], goal_concept_ids=['a'])


def candidate():
    return {'candidate_id': 'c1', 'concept_ids': ['a', 'b'], 'reason': '组合复查',
            'evidence_ids': ['e1'], 'relation_ids': ['r1'], 'uncertainties': []}


def decision():
    return {'decision': 'recommend', 'selected_candidate_id': None,
            'concept_ids': ['a'], 'reason': '依据e1复查数组', 'evidence_ids': ['e1'],
            'relation_ids': [], 'uncertainties': ['原诊断为模型推断']}


def response(value):
    return {'id': 'fake', 'choices': [{'finish_reason': 'stop',
             'message': {'content': json.dumps(value)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def client(self, store, transport):
        return CachedChatClient(base_url='https://example.com/v1', api_key='fake', model='test',
                                cache_dir=self.root/'cache', max_calls=6,
                                transport=RecordedTransport(store, transport), event_sink=store.api_event)

    def test_candidates_validate_scope_evidence_relations_and_duplicates(self):
        ctx = context(); valid = {'candidates': [candidate()], 'reason': '一组可用候选'}
        validate_candidates(ctx, valid, 3)
        bad = []
        v = deepcopy(valid); v['candidates'][0]['concept_ids'] = ['outside']; bad.append(v)
        v = deepcopy(valid); v['candidates'][0]['evidence_ids'] = ['missing']; bad.append(v)
        v = deepcopy(valid); v['candidates'][0]['relation_ids'] = 'missing'; bad.append(v)
        v = deepcopy(valid); v['candidates'].append(dict(candidate(), candidate_id='c2')); bad.append(v)
        v = deepcopy(valid); v['candidates'][0]['concept_ids'] = ['b']; bad.append(v)
        for v in bad:
            with self.subTest(v=v), self.assertRaises(ValueError):
                validate_candidates(ctx, v, 3)

    def test_selector_cannot_invent_or_modify_candidate(self):
        ctx = context(); d = decision(); d.update(selected_candidate_id='c1', concept_ids=['a', 'b'])
        validate_decision(ctx, d, [candidate()])
        for key, value in [('selected_candidate_id', 'missing'), ('concept_ids', ['a'])]:
            bad = deepcopy(d); bad[key] = value
            with self.assertRaises(ValueError): validate_decision(ctx, bad, [candidate()])

    def test_direct_requires_no_candidate_id_and_unknown_needs_uncertainty(self):
        ctx = context(); d = decision(); validate_decision(ctx, d)
        d['selected_candidate_id'] = 'invented'
        with self.assertRaises(ValueError): validate_decision(ctx, d)
        d = decision(); d.update(concept_ids=['c'], evidence_ids=[], uncertainties=[])
        with self.assertRaises(ValueError): validate_decision(ctx, d)
        d['uncertainties'] = ['未观察，需要诊断']
        validate_decision(ctx, d)

    def test_insufficient_evidence_is_valid_without_forced_target(self):
        d = decision(); d.update(decision='insufficient_evidence', concept_ids=[], evidence_ids=[])
        validate_decision(context(), d)
        d['concept_ids'] = ['a']
        with self.assertRaises(ValueError): validate_decision(context(), d)

    def test_model_handoff_records_actual_selection_origin(self):
        request = build_generation_request(context(), selected_concept_ids=['a'], reason='模型选择',
                                           selection_origin='model_two_stage')
        self.assertEqual(request['target_source'], 'model_two_stage')
        self.assertFalse(any('目标由调用方明确指定' in v for v in request['limitations']))

    def test_adapter_recommendation_stage_requires_and_applies_validator(self):
        calls = []
        def transport(*args): calls.append(1); return response(decision())
        client = CachedChatClient(base_url='https://example.com/v1', api_key='fake', model='test',
                                  cache_dir=self.root/'cache', transport=transport)
        with self.assertRaises(ValueError): client.complete('recommend_direct', 'p')
        self.assertEqual(calls, [])
        client.complete_validated('recommend_direct', 'p', lambda d: validate_decision(context(), d))
        self.assertEqual(len(calls), 1)

    def test_two_stage_and_direct_have_same_context_and_no_candidate_leak(self):
        calls = []
        def transport(url, key, payload, timeout):
            prompt = payload['messages'][0]['content']; calls.append(prompt)
            if 'TASK: candidates' in prompt:
                return response({'candidates': [candidate()], 'reason': 'ONLY_A_SENTINEL'})
            d = decision()
            if 'TASK: select' in prompt: d.update(selected_candidate_id='c1', concept_ids=['a', 'b'])
            return response(d)
        ctx = context()
        with RunStore(self.root/'run', {'test': 1}, 6) as store:
            report = run_comparison(store, self.client(store, transport), ctx, candidate_count=3)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(len(calls), 3)
        self.assertIn('ONLY_A_SENTINEL', calls[1])
        self.assertNotIn('ONLY_A_SENTINEL', calls[2])
        self.assertTrue(all(p.split('CONTEXT_JSON:\n')[1].split('\nEND_CONTEXT')[0] ==
                            calls[0].split('CONTEXT_JSON:\n')[1].split('\nEND_CONTEXT')[0] for p in calls))
        self.assertEqual(report['arms']['A']['requests'], 2)
        self.assertEqual(report['arms']['B']['requests'], 1)
        self.assertEqual(report['arms']['A']['usage']['total_tokens'], 30)
        self.assertEqual(report['arms']['B']['usage']['total_tokens'], 15)
        self.assertEqual(report['arms']['A']['generation_request']['target_source'], 'model_two_stage')

    @patch('time.sleep')
    def test_failed_A_does_not_block_B_and_resume_does_not_repeat_requests(self, sleep):
        def transport(url, key, payload, timeout):
            if 'TASK: candidates' in payload['messages'][0]['content']:
                return response({'candidates': [dict(candidate(), concept_ids=['outside'])], 'reason': 'bad'})
            return response(decision())
        with RunStore(self.root/'run', {'test': 1}, 6) as store:
            report = run_comparison(store, self.client(store, transport), context(), candidate_count=3)
            self.assertEqual(report['status'], 'partial')
            self.assertEqual(report['arms']['B']['status'], 'completed')
            self.assertEqual(store.state['steps']['A.select']['status'], 'blocked')
            self.assertEqual(len(store.state['attempts']), 3)
            self.assertEqual(store.state['attempts'][0]['status'], 'rejected')
            store.state['steps']['A.candidates']['elapsed_seconds'] = 12.0
            store.commit('test_duration_saved')
        with RunStore(self.root/'run', {'test': 1}, 6, resume=True) as store:
            client = self.client(store, lambda *a: self.fail('unexpected network'))
            resumed = run_comparison(store, client, context(), candidate_count=3)
            self.assertEqual(len(store.state['attempts']), 3)
            self.assertGreaterEqual(resumed['arms']['A']['step_elapsed_seconds'], 12.0)

    def test_completed_checkpoint_resumes_without_cache_or_network(self):
        def transport(url, key, payload, timeout):
            p = payload['messages'][0]['content']
            if 'TASK: candidates' in p: return response({'candidates': [candidate()], 'reason': '合理'})
            d = decision()
            if 'TASK: select' in p: d.update(selected_candidate_id='c1', concept_ids=['a', 'b'])
            return response(d)
        with RunStore(self.root/'run', {'test': 1}, 6) as store:
            run_comparison(store, self.client(store, transport), context(), candidate_count=3)
        for p in (self.root/'cache').glob('*.json'): p.unlink()
        with RunStore(self.root/'run', {'test': 1}, 6, resume=True) as store:
            report = run_comparison(store, self.client(store, lambda *a: self.fail('unexpected network')),
                                    context(), candidate_count=3)
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(len(store.state['attempts']), 3)

    def test_empty_candidates_are_valid_abstention_without_selector_call(self):
        def transport(url, key, payload, timeout):
            if 'TASK: candidates' in payload['messages'][0]['content']:
                return response({'candidates': [], 'reason': '没有合适组合'})
            return response(decision())
        with RunStore(self.root/'run', {'test': 1}, 6) as store:
            report = run_comparison(store, self.client(store, transport), context(), candidate_count=3)
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(report['arms']['A']['decision']['decision'], 'insufficient_evidence')
            self.assertEqual(len(store.state['attempts']), 2)

    def test_interrupted_selector_stays_unknown_on_resume_while_B_continues(self):
        def transport(url, key, payload, timeout):
            if 'TASK: candidates' in payload['messages'][0]['content']:
                return response({'candidates': [candidate()], 'reason': '合理'})
            raise KeyboardInterrupt()
        with RunStore(self.root/'run', {'test': 1}, 6) as store:
            with self.assertRaises(KeyboardInterrupt):
                run_comparison(store, self.client(store, transport), context(), candidate_count=3)
            self.assertEqual(store.state['status'], 'interrupted')
        calls = []
        def only_B(url, key, payload, timeout):
            calls.append(payload['messages'][0]['content'])
            self.assertIn('TASK: direct', calls[-1])
            return response(decision())
        with RunStore(self.root/'run', {'test': 1}, 6, resume=True) as store:
            report = run_comparison(store, self.client(store, only_B), context(), candidate_count=3)
            self.assertEqual(report['status'], 'partial')
            self.assertEqual(report['arms']['B']['status'], 'completed')
            self.assertEqual(store.state['attempts'][1]['status'], 'unknown')
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(store.state['attempts']), 3)

    def test_cli_preview_needs_no_env_and_execute_resume_uses_checkpoint(self):
        for name, obj in [('catalog', catalog()), ('profile', profile())]:
            (self.root/(name+'.json')).write_text(json.dumps(obj), encoding='utf-8')
        args = ['--catalog', str(self.root/'catalog.json'), '--profile', str(self.root/'profile.json'),
                '--student', 's1', '--scope', 'a', 'b', 'c', '--goals', 'a',
                '--env-file', str(self.root/'missing.env'), '--output', str(self.root/'cli-run'),
                '--cache', str(self.root/'cli-cache')]
        preview = subprocess.run([sys.executable, 'compare_recommendation.py', *args], capture_output=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse((self.root/'cli-run').exists())
        (self.root/'missing.env').write_text('ZZZ_BASE_URL=https://example.com/v1\nZZZ_API_KEY=fake\nZZZ_MODEL=test\n', encoding='utf-8')
        bootstrap = '''import runpy,json
import profiling.api_client as api
def fake(url,key,payload,timeout):
 p=payload['messages'][0]['content']
 d={'decision':'recommend','selected_candidate_id':None,'concept_ids':['a'],'reason':'evidence','evidence_ids':['e1'],'relation_ids':[],'uncertainties':[]}
 if 'TASK: candidates' in p:
  d={'candidates':[{'candidate_id':'c1','concept_ids':['a'],'reason':'candidate','evidence_ids':['e1'],'relation_ids':[],'uncertainties':[]}],'reason':'ok'}
 elif 'TASK: select' in p:d['selected_candidate_id']='c1'
 return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(d)}}]}
api.send_chat=fake
runpy.run_path('compare_recommendation.py',run_name='__main__')
'''
        first = subprocess.run([sys.executable, '-c', bootstrap, *args, '--execute'], capture_output=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        blocked = bootstrap.replace('api.send_chat=fake', "api.send_chat=lambda *a: (_ for _ in ()).throw(AssertionError('network'))")
        resumed = subprocess.run([sys.executable, '-c', blocked, *args, '--execute', '--resume'], capture_output=True)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(RunStore.inspect(self.root/'cli-run')['requests_reserved'], 3)
        status = subprocess.run([sys.executable, 'compare_recommendation.py', '--status', '--output', str(self.root/'cli-run')], capture_output=True)
        self.assertEqual(status.returncode, 0, status.stderr)
