import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from profiling.api_client import CachedChatClient
from profiling.run_store import RecordedTransport, RunStore
from recommendation.runner import run_recommendation
from test_recommendation import catalog, profile
from test_recommendation_comparison import context, decision, response


class RecommendationRunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def client(self, store, transport):
        return CachedChatClient(base_url='https://example.com/v1', api_key='fake', model='test',
            cache_dir=self.root/'cache', max_calls=2,
            transport=RecordedTransport(store, transport), event_sink=store.api_event)

    def test_direct_only_records_provenance_and_handoff_without_mutating_profile(self):
        ctx = context(); before = json.dumps(ctx, sort_keys=True)
        calls = []
        def transport(url, key, payload, timeout):
            calls.append(payload)
            self.assertIn('TASK: direct', payload['messages'][0]['content'])
            self.assertNotIn('CANDIDATES_JSON:', payload['messages'][0]['content'])
            return response(decision())
        with RunStore(self.root/'run', {'inputs': {'profile': 'hash'}}, 2) as store:
            result = run_recommendation(store, self.client(store, transport), ctx)
            self.assertEqual(set(store.state['steps']), {'B.direct'})
            self.assertEqual(result['run_id'], store.state['run_id'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'recommended')
        self.assertEqual(result['execution_status'], 'completed')
        self.assertEqual(result['decision_kind'], 'model_inference')
        self.assertEqual(result['identity']['inputs']['profile'], 'hash')
        self.assertEqual(result['metrics']['requests'], 1)
        self.assertEqual(result['generation_request']['recommendation_id'], result['recommendation_id'])
        self.assertEqual(result['generation_request']['target_source'], 'model_direct')
        self.assertEqual(json.loads((self.root/'run/generation-request.json').read_text(encoding='utf-8')), result['generation_request'])
        self.assertEqual(json.dumps(ctx, sort_keys=True), before)

    def test_insufficient_evidence_is_completed_without_handoff(self):
        d = dict(decision(), decision='insufficient_evidence', concept_ids=[], evidence_ids=[], reason='需要明确目标')
        with RunStore(self.root/'run', {'test': 1}, 2) as store:
            r = run_recommendation(store, self.client(store, lambda *a: response(d)), context())
            self.assertEqual(store.state['status'], 'completed')
        self.assertEqual(r['status'], 'insufficient_evidence')
        self.assertIsNone(r['generation_request'])
        self.assertFalse((self.root/'run/generation-request.json').exists())

    @patch('time.sleep')
    def test_rejected_retries_are_bounded_and_resume_does_not_reset(self, sleep):
        bad = dict(decision(), concept_ids=['outside'])
        with RunStore(self.root/'run', {'test': 1}, 2) as store:
            r = run_recommendation(store, self.client(store, lambda *a: response(bad)), context())
            self.assertEqual(len(store.state['attempts']), 2)
            self.assertTrue(all(a['status'] == 'rejected' for a in store.state['attempts']))
        self.assertEqual(r['status'], 'failed')
        self.assertIsNotNone(r['error'])
        self.assertIsNone(r['decision'])
        self.assertFalse((self.root/'run/generation-request.json').exists())
        with RunStore(self.root/'run', {'test': 1}, 2, resume=True) as store:
            r = run_recommendation(store, self.client(store, lambda *a: self.fail('network')), context())
            self.assertEqual(r['metrics']['requests'], 2)
            self.assertEqual(r['status'], 'failed')

    @patch('time.sleep')
    def test_invalid_then_valid_retries_once(self, sleep):
        replies = iter([dict(decision(), evidence_ids=['missing']), decision()])
        with RunStore(self.root/'run', {'test': 1}, 2) as store:
            r = run_recommendation(store, self.client(store, lambda *a: response(next(replies))), context())
        self.assertEqual(r['status'], 'recommended')
        self.assertEqual(r['metrics']['requests'], 2)

    def test_resume_completed_without_cache_preserves_round_id(self):
        with RunStore(self.root/'run', {'test': 1}, 2) as store:
            first = run_recommendation(store, self.client(store, lambda *a: response(decision())), context())
        for p in (self.root/'cache').glob('*.json'): p.unlink()
        with RunStore(self.root/'run', {'test': 1}, 2, resume=True) as store:
            r = run_recommendation(store, self.client(store, lambda *a: self.fail('network')), context())
        self.assertEqual(r['recommendation_id'], first['recommendation_id'])
        self.assertEqual(r['created_at'], first['created_at'])
        self.assertEqual(r['metrics']['requests'], 1)
        self.assertEqual(r['status'], 'recommended')

    def test_unknown_request_not_resent_after_interruption(self):
        def interrupt(*args): raise KeyboardInterrupt()
        with RunStore(self.root/'run', {'test': 1}, 2) as store:
            with self.assertRaises(KeyboardInterrupt):
                run_recommendation(store, self.client(store, interrupt), context())
            self.assertEqual(store.state['status'], 'interrupted')
        with RunStore(self.root/'run', {'test': 1}, 2, resume=True) as store:
            r = run_recommendation(store, self.client(store, lambda *a: self.fail('network')), context())
            self.assertEqual(store.state['attempts'][0]['status'], 'unknown')
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['metrics']['requests'], 1)
        self.assertIsNone(r['generation_request'])

    def test_received_response_replayed_without_network(self):
        # Crash after transport persisted the response but before business validation.
        from recommendation.comparison import make_model_input
        with RunStore(self.root/'run', {'test': 1}, 2) as store:
            store.start_step('B.direct', {})
            client = self.client(store, lambda *a: response(decision()))
            def crash(event): raise KeyboardInterrupt()
            client.event_sink = crash
            with self.assertRaises(KeyboardInterrupt):
                client.complete_validated('recommend_direct', make_model_input(context(), 'direct'), lambda v: None)
            self.assertEqual(store.state['attempts'][0]['status'], 'received')
        for p in (self.root/'cache').glob('*.json'): p.unlink()
        with RunStore(self.root/'run', {'test': 1}, 2, resume=True) as store:
            r = run_recommendation(store, self.client(store, lambda *a: self.fail('network')), context())
            self.assertTrue(any(e['event'] == 'response_replayed' for e in store.snapshot()['events']))
        self.assertEqual(r['status'], 'recommended')
        self.assertEqual(r['metrics']['requests'], 1)

    def test_cli_preview_execute_resume_inspect_and_changed_inputs(self):
        for name, value in [('catalog', catalog()), ('profile', profile())]:
            (self.root/(name+'.json')).write_text(json.dumps(value), encoding='utf-8')
        args = ['--catalog', str(self.root/'catalog.json'), '--profile', str(self.root/'profile.json'),
            '--student', 's1', '--scope', 'a', 'b', 'c', '--goals', 'a',
            '--output', str(self.root/'cli'), '--cache', str(self.root/'cli-cache'),
            '--env-file', str(self.root/'missing.env')]
        def run(*extra, bootstrap=None):
            command = ['-c', bootstrap] if bootstrap else ['run_recommendation.py']
            return subprocess.run([sys.executable, *command, *args, *extra], capture_output=True)
        preview = run()
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse((self.root/'cli').exists())
        (self.root/'missing.env').write_text('ZZZ_BASE_URL=https://example.com/v1\nZZZ_API_KEY=fake\nZZZ_MODEL=test\n', encoding='utf-8')
        bootstrap = '''import runpy,json
import profiling.api_client as api
def fake(*args):
 return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(VALUE)}}]}
api.send_chat=fake
runpy.run_path('run_recommendation.py',run_name='__main__')
'''.replace('VALUE', repr(decision()))
        first = run('--execute', bootstrap=bootstrap)
        self.assertEqual(first.returncode, 0, first.stderr)
        blocked = bootstrap.replace('api.send_chat=fake', "api.send_chat=lambda *a: (_ for _ in ()).throw(AssertionError('network'))")
        resumed = run('--execute', '--resume', bootstrap=blocked)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(RunStore.inspect(self.root/'cli')['requests_reserved'], 1)
        changed = run('--execute', '--resume', '--requirement', 'changed', bootstrap=blocked)
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn(b'mismatch', changed.stderr)
        (self.root/'missing.env').unlink()
        for mode in ['--status', '--trace']:
            r = run(mode)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_abstention_exits_zero_but_invalid_response_exits_nonzero(self):
        (self.root/'catalog.json').write_text(json.dumps(catalog()), encoding='utf-8')
        (self.root/'test.env').write_text('ZZZ_BASE_URL=https://example.com/v1\nZZZ_API_KEY=fake\nZZZ_MODEL=test\n', encoding='utf-8')
        abstain = dict(decision(), decision='insufficient_evidence', concept_ids=[], evidence_ids=[], reason='不足')
        for name, value, exit_ok in [('abstain', abstain, True), ('invalid', dict(decision(), concept_ids=['outside']), False)]:
            with self.subTest(name=name):
                bootstrap = '''import runpy,json,time
import profiling.api_client as api
time.sleep=lambda *a:None
def fake(*args):
 return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(VALUE)}}]}
api.send_chat=fake
runpy.run_path('run_recommendation.py',run_name='__main__')
'''.replace('VALUE', repr(value))
                run = subprocess.run([sys.executable, '-c', bootstrap, '--execute',
                    '--catalog', str(self.root/'catalog.json'), '--student', 's1', '--scope', 'a',
                    '--env-file', str(self.root/'test.env'), '--output', str(self.root/name),
                    '--cache', str(self.root/(name+'-cache'))], capture_output=True)
                self.assertEqual(run.returncode == 0, exit_ok, run.stderr)
                self.assertFalse((self.root/name/'generation-request.json').exists())
                report = json.loads((self.root/name/'recommendation.json').read_text(encoding='utf-8'))
                self.assertEqual(report['status'], 'insufficient_evidence' if exit_ok else 'failed')
