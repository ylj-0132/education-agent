import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from profiling.run_store import RunStore, fingerprint
from generation.local_java import prepare_review, minimal_env, bounded_process, verify_local


class LocalJavaTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup); self.root = Path(t.name)
        self.q = {'reference_code': 'public class Main {}', 'statement': '求和'}
        self.cases = [{'input': '1 2\n', 'expected_output': '3\n', 'basis': '独立计算1+2=3'}]
        self.tools = {'java': sys.executable, 'javac': sys.executable}

    def package(self):
        return prepare_review(self.q, self.cases, self.tools, question_id='q1')

    def test_approval_binds_code_cases_tools_and_limits(self):
        a = self.package()
        self.cases[0]['expected_output'] = '4\n'
        self.assertNotEqual(a['approval_sha256'], self.package()['approval_sha256'])
        self.cases[0]['basis'] = ''
        with self.assertRaises(ValueError): self.package()
        with RunStore(self.root/'run', {'test': 1}, 1) as store:
            with self.assertRaises(ValueError):
                verify_local(store, a, 'wrong', runner=lambda *a, **k: self.fail('execution'))

    def test_minimal_env_removes_credentials_java_options_and_profile(self):
        with patch.dict(os.environ, {'ZZZ_API_KEY': 'secret', 'JAVA_TOOL_OPTIONS': '-evil', 'CLASSPATH': 'x'}, clear=False):
            env = minimal_env(self.root, Path(sys.executable).parent)
        self.assertNotIn('ZZZ_API_KEY', env); self.assertNotIn('JAVA_TOOL_OPTIONS', env)
        self.assertNotIn('CLASSPATH', env); self.assertNotIn('USERPROFILE', env)
        self.assertEqual(env['TEMP'], str(self.root))

    def test_bounded_process_success_timeout_and_output(self):
        env = minimal_env(self.root, Path(sys.executable).parent)
        for script, expected in [("print('ok')", 'completed'), ('import time;time.sleep(10)', 'timeout'),
                                 ("import os;os.write(1,b'x'*100000)", 'output_limit')]:
            with self.subTest(expected=expected):
                result = bounded_process([sys.executable, '-c', script], cwd=self.root, env=env,
                    stdin='', timeout=.8, output_limit=1024)
                self.assertEqual(result['status'], expected)
                self.assertLessEqual(len(result['stdout'].encode())+len(result['stderr'].encode()), 1024)

    def test_verified_cases_save_results_and_resume_without_process(self):
        calls = []
        def runner(cmd, **kwargs):
            calls.append(cmd)
            return {'status': 'completed', 'returncode': 0, 'stdout': '' if len(calls)==1 else '3\n', 'stderr': ''}
        p = self.package()
        with RunStore(self.root/'run', {'test': 1}, 1) as store:
            result = verify_local(store, p, p['approval_sha256'], runner=runner)
        self.assertEqual(result['status'], 'tests_passed')
        self.assertFalse(result['sandboxed']); self.assertFalse(result['ready_for_student'])
        self.assertEqual(len(calls), 2)
        self.assertIn('-proc:none', calls[0])
        with RunStore(self.root/'run', {'test': 1}, 1, resume=True) as store:
            resumed = verify_local(store, p, p['approval_sha256'], runner=lambda *a, **k: self.fail('rerun'))
        self.assertEqual(resumed['status'], 'tests_passed')

    def test_compile_failure_stops_before_running_and_unknown_not_repeated(self):
        p = self.package()
        with RunStore(self.root/'run', {'test': 1}, 1) as store:
            r = verify_local(store, p, p['approval_sha256'], runner=lambda *a, **k: {
                'status': 'completed', 'returncode': 1, 'stdout': '', 'stderr': 'syntax error'})
            self.assertEqual(r['status'], 'compile_failed')
            self.assertEqual(len(store.state['steps']), 1)
        with RunStore(self.root/'interrupted', {'test': 1}, 1) as store:
            def crash(*a, **k): raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt): verify_local(store, p, p['approval_sha256'], runner=crash)
        with RunStore(self.root/'interrupted', {'test': 1}, 1, resume=True) as store:
            r = verify_local(store, p, p['approval_sha256'], runner=lambda *a, **k: self.fail('rerun'))
            self.assertEqual(r['status'], 'execution_unknown')

    def test_cli_preview_and_missing_approval_never_execute(self):
        from generation.workflow import run_generation
        from test_generation import request, question, review
        from test_recommendation_comparison import response
        from profiling.api_client import CachedChatClient
        from profiling.run_store import RecordedTransport
        outputs = iter([question(), review(question())])
        with RunStore(self.root/'generation', {'test': 1}, 8) as store:
            client = CachedChatClient(base_url='https://example.com/v1', api_key='fake', model='test',
                cache_dir=self.root/'cache', transport=RecordedTransport(store, lambda *a: response(next(outputs))), event_sink=store.api_event)
            run_generation(store, client, request())
        (self.root/'cases.json').write_text(json.dumps(self.cases), encoding='utf-8')
        (self.root/'jdk/bin').mkdir(parents=True)
        for name in ('java.exe', 'javac.exe'): (self.root/'jdk/bin'/name).write_bytes(b'not executable')
        args = ['--generation-run', str(self.root/'generation'), '--cases', str(self.root/'cases.json'),
            '--java-home', str(self.root/'jdk'), '--output', str(self.root/'verify')]
        r = subprocess.run([sys.executable, 'verify_java.py', *args], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b'approval_sha256', r.stdout)
        self.assertFalse((self.root/'verify').exists())
        r = subprocess.run([sys.executable, 'verify_java.py', *args, '--execute'], capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root/'verify').exists())

    @unittest.skipUnless(os.environ.get('LOCAL_JAVA_TEST_HOME'), 'explicit local JDK smoke test only')
    def test_real_jdk_with_our_known_small_fixture(self):
        home = Path(os.environ['LOCAL_JAVA_TEST_HOME'])/'bin'
        q = {'statement': '固定测试：输出1+2', 'reference_code':
             'public class Main { public static void main(String[] args) { System.out.println(1+2); } }'}
        p = prepare_review(q, self.cases, {'java': home/'java.exe', 'javac': home/'javac.exe'}, question_id='developer-smoke-only')
        with RunStore(self.root/'real', {'test': 'known-fixture'}, 1) as store:
            result = verify_local(store, p, p['approval_sha256'])
        self.assertEqual(result['status'], 'tests_passed', result)
