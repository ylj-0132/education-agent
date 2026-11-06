from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from profiling.api_client import CachedChatClient
from recommendation.comparison import make_model_input, make_prompt, run_comparison
from recommendation.sweep import build_cases, run_sweep
from test_recommendation_comparison import context, decision, response


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_single_and_split_have_identical_text_and_data(self):
        for task in ('candidates', 'direct'):
            single = make_model_input(context(), task, message_layout='single_user')
            split = make_model_input(context(), task, message_layout='split_roles')
            self.assertEqual([v['role'] for v in split], ['system', 'user'])
            self.assertEqual(single, ''.join(v['content'] for v in split))
            self.assertNotIn('CONTEXT_JSON:', split[0]['content'])
            self.assertIn('CONTEXT_JSON:', split[1]['content'])
            self.assertIn('"reason"', split[0]['content'])

    def test_client_preserves_string_payload_and_accepts_role_messages(self):
        received = []
        def transport(url, key, payload, timeout):
            received.append(payload); return response(decision())
        client = CachedChatClient(base_url='https://example.com/v1', api_key='fake', model='test',
                                  cache_dir=self.root/'cache', transport=transport)
        client.complete_validated('recommend_direct', 'plain', lambda v: None)
        self.assertEqual(received[0]['messages'], [{'role': 'user', 'content': 'plain'}])
        messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'data'}]
        original = deepcopy(messages)
        client.complete_validated('recommend_direct', messages, lambda v: None)
        self.assertEqual(received[1]['messages'], messages)
        self.assertEqual(messages, original)
        with self.assertRaises(ValueError):
            client.complete_validated('recommend_direct', [{'role': 'tool', 'content': 'bad'}], lambda v: None)
        self.assertEqual(len(received), 2)


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = json.loads(Path('config/java-basics.json').read_text(encoding='utf-8'))
        from test_recommendation import profile
        self.profile = profile()
        mapping = {'a': 'java.syntax', 'b': 'java.input'}
        self.profile['concepts'] = {mapping[k]: self.catalog['concepts'][mapping[k]] for k in mapping}
        for o in self.profile['observations']: o['concept_id'] = mapping[o['concept_id']]
        self.profile['summaries'] = {mapping[k]: v for k, v in self.profile['summaries'].items()}
        self.cases = build_cases(self.catalog, self.profile)
        self.settings = {'ZZZ_BASE_URL': 'https://example.com/v1', 'ZZZ_API_KEY': 'fake', 'ZZZ_MODEL': 'test'}

    def test_case_provenance_and_cold_start_do_not_fabricate_observations(self):
        self.assertEqual(len(self.cases), 3)
        self.assertEqual(self.cases[0]['context']['allowed_concept_ids'], ['java.syntax'])
        for case in self.cases[1:]:
            self.assertEqual(case['origin'], 'cold_start_no_history')
            self.assertEqual(case['context']['evidence'], [])
            self.assertTrue(all(not v['observations'] for v in case['context']['concept_context'].values()))
        self.assertTrue(any(not e['foundation_in_scope'] for e in
                            self.cases[2]['context']['relations']['prerequisites']))

    def test_sweep_balances_layouts_separates_caches_and_resumes_without_network(self):
        calls = []
        def transport(url, key, payload, timeout):
            messages = payload['messages']; text = ''.join(m['content'] for m in messages)
            data = json.loads(text.split('CONTEXT_JSON:\n')[1].split('\nEND_CONTEXT')[0])
            target = data['goal_concept_ids'][0]
            d = {'decision': 'recommend', 'selected_candidate_id': None, 'concept_ids': [target],
                 'reason': 'test recommendation', 'evidence_ids': [], 'relation_ids': [],
                 'uncertainties': ['test uncertainty']}
            if 'TASK: candidates' in text:
                d = {'candidates': [{'candidate_id': 'c1', 'concept_ids': [target],
                     'reason': 'test candidate', 'evidence_ids': [], 'relation_ids': [],
                     'uncertainties': ['test uncertainty']}], 'reason': 'test'}
            elif 'TASK: select' in text: d['selected_candidate_id'] = 'c1'
            calls.append(messages)
            return response(d)
        report = run_sweep(self.cases, self.settings, self.root/'suite', transport=transport)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['requests'], 18)
        self.assertEqual(len(calls), 18)
        self.assertEqual(sum(len(v) == 2 for v in calls), 9)
        self.assertEqual(report['role_probe']['matched_logical_text_pairs'], 6)
        self.assertEqual(report['role_probe']['excluded_mediated_stage'], 'A.select')
        second = run_sweep(self.cases, self.settings, self.root/'suite', resume=True,
                           transport=lambda *a: self.fail('unexpected network'))
        self.assertEqual(second['requests'], 18)
        self.assertEqual(len(calls), 18)
        saved = json.loads((self.root/'suite/suite.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['requests'], 18)
