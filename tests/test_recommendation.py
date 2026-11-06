from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from recommendation.context import prepare_context, build_generation_request


def catalog():
    return {
        'version': 'test-v1', 'concepts': {'a': '数组', 'b': '循环', 'c': '状态转移'},
        'relation_sources': {'guide': {'title': '设计依据'}},
        'prerequisites': [{'id': 'p1', 'from': 'a', 'to': 'c',
                           'applies_when': '使用数组保存状态', 'reason': '需要数组访问',
                           'status': 'draft', 'hard_gate': False, 'source_ids': ['guide']}],
        'associations': [{'id': 'r1', 'concept_ids': ['a', 'b'],
                          'use_case': '遍历数组', 'status': 'draft', 'source_ids': ['guide']}],
    }


def profile():
    return {
        'status': 'completed', 'failed_steps': [], 'student_id': 's1',
        'model_id': 'fake-test', 'prompt_version': 'test-prompt',
        'concepts': {'a': '数组', 'b': '循环'},
        'evidence': [
            {'record_id': 'e1', 'student_id': 's1', 'split': 'train', 'problem_id': 'q1',
             'question': '遍历数组', 'program': 'CODE1', 'source': {'file': 'f', 'index': 0},
             'annotations': {'desc': 'EXTERNAL_DESC_SENTINEL', 'error_desc': 'EXTERNAL_ERROR_SENTINEL'}},
            {'record_id': 'e2', 'student_id': 's1', 'split': 'train', 'problem_id': 'q2',
             'question': '循环', 'program': 'CODE2', 'source': {'file': 'f', 'index': 1}},
        ],
        'observations': [
            {'concept_id': 'a', 'status': 'Bad', 'description': '数组索引有误',
             'kind': 'model_inference', 'evidence_id': 'e1', 'student_id': 's1'},
            {'concept_id': 'b', 'status': 'Good', 'description': '本题循环边界正确',
             'kind': 'model_inference', 'evidence_id': 'e2', 'student_id': 's1'},
        ],
        'summaries': {'a': {'evaluation': '本题索引待复查', 'kind': 'model_inference',
                            'counts': {'Bad': 1}, 'evidence_ids': ['e1']}},
    }


class RecommendationTests(unittest.TestCase):
    def prepare(self, **kwargs):
        values = dict(student_id='s1', allowed_concept_ids=['a', 'b', 'c'],
                      goal_concept_ids=['c'], requirements=['仅基础练习'], profile=profile())
        values.update(kwargs)
        return prepare_context(catalog(), **values)

    def test_states_remain_evidence_not_mastery_probabilities(self):
        context = self.prepare()
        self.assertEqual(context['status'], 'awaiting_policy')
        self.assertEqual(context['concept_context']['a']['counts'], {'Bad': 1})
        self.assertEqual(context['concept_context']['b']['observations'][0]['status'], 'Good')
        self.assertEqual(context['concept_context']['c']['evidence_status'], 'no_observation')
        self.assertEqual(context['concept_context']['c']['counts'], {})
        self.assertEqual(context['concept_context']['c']['summary'], None)
        self.assertNotIn('mastery_probability', json.dumps(context))
        self.assertNotIn('selected_concept_ids', context)
        self.assertEqual(context['selection_policy'], 'model_direct')

    def test_unknown_and_conflicting_observations_are_not_collapsed(self):
        p = profile()
        p['observations'] += [dict(p['observations'][0], status='Unknown', evidence_id='e2')]
        p['summaries'] = {}
        state = self.prepare(profile=p)['concept_context']['a']
        self.assertEqual(state['counts'], {'Bad': 1, 'Unknown': 1})
        self.assertEqual(len(state['observations']), 2)

    def test_only_relevant_evidence_is_passed_and_sources_not_mutated(self):
        p = profile(); before = deepcopy(p)
        context = self.prepare(profile=p, allowed_concept_ids=['a'], goal_concept_ids=['a'])
        self.assertEqual([r['record_id'] for r in context['evidence']], ['e1'])
        self.assertEqual(context['evidence'][0]['program'], 'CODE1')
        self.assertNotIn('EXTERNAL_', json.dumps(context))
        self.assertNotIn('annotations', context['evidence'][0])
        context['concept_context']['a']['observations'][0]['description'] = 'edited'
        self.assertEqual(p, before)

    def test_absent_profile_is_unknown_not_a_fake_student_profile(self):
        context = self.prepare(profile=None)
        self.assertEqual(context['profile_source'], None)
        self.assertEqual(context['evidence'], [])
        self.assertTrue(all(v['evidence_status'] == 'no_observation'
                            for v in context['concept_context'].values()))

    def test_partial_foreign_test_and_broken_evidence_are_rejected(self):
        bad = []
        p = profile(); p['status'] = 'partial'; bad.append(p)
        p = profile(); p['failed_steps'] = ['extract:e3']; bad.append(p)
        p = profile(); p['student_id'] = 'other'; bad.append(p)
        p = profile(); p['evidence'][0]['student_id'] = 'other'; bad.append(p)
        p = profile(); p['evidence'][0]['split'] = 'test'; bad.append(p)
        p = profile(); p['observations'][0]['evidence_id'] = 'missing'; bad.append(p)
        p = profile(); p['observations'][0]['student_id'] = 'other'; bad.append(p)
        p = profile(); p['observations'][0]['concept_id'] = 'invented'; bad.append(p)
        p = profile(); p['summaries']['a']['evidence_ids'] = ['e2']; bad.append(p)
        p = profile(); p['summaries']['a']['counts'] = {'Good': 1}; bad.append(p)
        p = profile(); p['evidence'].append(deepcopy(p['evidence'][0])); bad.append(p)
        p = profile(); p['concepts']['a'] = 'different definition'; bad.append(p)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.prepare(profile=value)

    def test_scope_and_goal_validation(self):
        for update in [dict(allowed_concept_ids=[]), dict(allowed_concept_ids=['a', 'a']),
                       dict(allowed_concept_ids=['unknown']), dict(goal_concept_ids=['unknown']),
                       dict(allowed_concept_ids=['a'], goal_concept_ids=['b']),
                       dict(requirements='not a list')]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.prepare(**update)

    def test_provenance_requires_file_and_index_and_discards_extra_annotations(self):
        p = profile()
        p['evidence'][0]['source'] = {'index': 0}
        with self.assertRaises(ValueError):
            self.prepare(profile=p)
        p = profile()
        p['evidence'][0]['source']['extra_analysis'] = 'EXTERNAL_SOURCE_SENTINEL'
        context = self.prepare(profile=p)
        self.assertNotIn('EXTERNAL_SOURCE_SENTINEL', json.dumps(context))

    def test_conditional_prerequisite_is_not_applied_or_expanded_automatically(self):
        context = self.prepare(allowed_concept_ids=['c'])
        edge = context['relations']['prerequisites'][0]
        self.assertEqual((edge['from'], edge['to']), ('a', 'c'))
        self.assertFalse(edge['foundation_in_scope'])
        self.assertEqual(edge['applicability'], 'not_assessed')
        self.assertFalse(edge['hard_gate'])
        self.assertEqual(context['allowed_concept_ids'], ['c'])
        self.assertEqual(context['evidence'], [])

    def test_association_is_undirected_and_scope_limited(self):
        c = catalog(); c['associations'][0]['concept_ids'] = ['b', 'a']
        context = prepare_context(c, student_id='s1', allowed_concept_ids=['a', 'b'])
        self.assertEqual(set(context['relations']['associations'][0]['concept_ids']), {'a', 'b'})
        context = self.prepare(allowed_concept_ids=['b'], goal_concept_ids=[])
        self.assertEqual(context['relations']['associations'], [])

    def test_invalid_relation_endpoints_duplicates_cycles_or_sources_rejected(self):
        bad = []
        c = catalog(); c['prerequisites'][0]['from'] = 'unknown'; bad.append(c)
        c = catalog(); c['prerequisites'].append(dict(c['prerequisites'][0], id='p2', **{'from': 'c', 'to': 'a'})); bad.append(c)
        c = catalog(); c['associations'].append(dict(c['associations'][0], id='r2', concept_ids=['b', 'a'])); bad.append(c)
        c = catalog(); c['prerequisites'][0]['source_ids'] = ['missing']; bad.append(c)
        for c in bad:
            with self.subTest(c=c), self.assertRaises(ValueError):
                prepare_context(c, student_id='s1', allowed_concept_ids=['a'])

    def test_explicit_bundle_handoff_keeps_requirements_and_relevant_evidence(self):
        context = self.prepare()
        request = build_generation_request(context, selected_concept_ids=['a', 'b'],
                                           reason='教师指定数组与循环组合', evidence_ids=['e1'])
        self.assertEqual(request['target_source'], 'explicit_selection')
        self.assertEqual(request['target_concept_ids'], ['a', 'b'])
        self.assertEqual(request['requirements'], ['仅基础练习'])
        self.assertEqual(request['reason_evidence_ids'], ['e1'])
        self.assertEqual(request['previous_submission_problem_ids'], ['q1', 'q2'])
        self.assertNotIn('EXTERNAL_', json.dumps(request))
        request = build_generation_request(context, selected_concept_ids=['a'],
                                           reason='复查数组索引', evidence_ids=[])
        self.assertEqual([r['record_id'] for r in request['evidence']], ['e1'])

    def test_handoff_rejects_outside_scope_duplicates_and_unrelated_reason_evidence(self):
        context = self.prepare(allowed_concept_ids=['a', 'b'], goal_concept_ids=[])
        for ids, reason, evidence in [(['c'], 'x', []), (['a', 'a'], 'x', []),
                                       ([], 'x', []), (['a'], '', []),
                                       (['a'], 'x', ['e2']), (['a'], 'x', ['missing'])]:
            with self.subTest(ids=ids, evidence=evidence), self.assertRaises(ValueError):
                build_generation_request(context, selected_concept_ids=ids,
                                         reason=reason, evidence_ids=evidence)

    def test_cli_is_offline_creates_new_output_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'catalog.json').write_text(json.dumps(catalog()), encoding='utf-8')
            (root/'profile.json').write_text(json.dumps(profile()), encoding='utf-8')
            args = [sys.executable, 'prepare_recommendation.py', '--catalog', str(root/'catalog.json'),
                    '--student', 's1', '--scope', 'a', 'b', '--goals', 'a',
                    '--profile', str(root/'profile.json'), '--output', str(root/'out'),
                    '--select', 'a', 'b', '--reason', 'explicit demo']
            result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            context = json.loads((root/'out/context.json').read_text(encoding='utf-8'))
            self.assertEqual(context['status'], 'awaiting_policy')
            manifest = json.loads((root/'out/manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['network_calls'], 0)
            self.assertEqual(manifest['inputs']['profile']['sha256'].__len__(), 64)
            self.assertTrue((root/'out/generation-request.json').is_file())
            self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
            args[args.index('--student')+1] = 'other'
            args[args.index('--output')+1] = str(root/'invalid')
            self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
            self.assertFalse((root/'invalid').exists())
