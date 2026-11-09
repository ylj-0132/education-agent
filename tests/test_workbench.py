import json
import tempfile
import threading
import time
import unittest
import http.client
import sys
from pathlib import Path
from webapp.service import WorkbenchService, run_command
from webapp.server import make_server

class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'config').mkdir()
        (self.root/'config/java-basics.json').write_text('{"concepts": []}')
        (self.root/'runs/x').mkdir(parents=True)
        (self.root/'runs/x/profile.json').write_text('{"student_id":"s1"}')
        (self.root/'.env').write_text('SECRET=private')
        self.calls=[]
        self.service=WorkbenchService(self.root, runner=self.runner)
    def runner(self,args,cwd):
        self.calls.append(args)
        return 0,'ok',''
    def wait(self,job):
        for _ in range(100):
            job=self.service.get_job(job['id'])
            if job['status'] not in ('queued','running'):return job
            time.sleep(.01)
        self.fail('job did not finish')
    def test_artifact_allowlist(self):
        self.assertEqual(self.service.artifact('runs/x/profile.json')['student_id'],'s1')
        for name in ('../.env','.env','runs/x/trace.json','C:/secret.json'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.service.artifact(name)
    def test_reject_invalid_launch(self):
        for data in ({'operation':'shell','mode':'preview','fields':{}},
                     {'operation':'profile','mode':'execute','fields':{}},
                     {'operation':'verify_reference','mode':'execute','confirm':True,'fields':{}},
                     {'operation':'profile','mode':'preview','fields':{'env_file':'.env'}}):
            with self.subTest(data=data),self.assertRaises(ValueError):self.service.launch(data)
        self.assertEqual(self.calls,[])
    def test_preview_never_executes(self):
        job=self.wait(self.service.launch({'operation':'profile','mode':'preview','fields':{'student':'s1'}}))
        self.assertEqual(job['status'],'succeeded')
        self.assertNotIn('--execute',self.calls[0])
        self.assertNotIn('--save',self.calls[0])
    def test_array_cannot_inject_execution_switch(self):
        with self.assertRaises(ValueError):
            self.service.launch({'operation':'recommend','mode':'preview','fields':{'scope':['--execute']}})
        self.assertEqual(self.calls,[])
    def test_java_home_and_usage_manifest(self):
        args,_=self.service.command({'operation':'verify_student','mode':'preview','fields':{}},'test')
        self.assertIn('--java-home',args)
        folder=self.root/'artifacts/usage-history';folder.mkdir(parents=True)
        (folder/'s1.json').write_text('{"schema":"question-usage-sources-v1","student_id":"s1"}')
        self.assertEqual(len(self.service.bootstrap()['usage_files']),1)
    def test_submission_draft(self):
        answer=dict(schema='student-answer-v1',origin='test_fixture',submission_id='x',student_id='s1',question_id='q1',question_sha256='a'*64,program='class Main {}',submitted_at=None)
        path=self.service.save_submission_draft(answer)
        self.assertEqual(json.loads((self.root/path).read_text())['origin'],'test_fixture')
        with self.assertRaises(ValueError):self.service.save_submission_draft(dict(answer,origin='fake'))
    def test_real_python_unicode_output(self):
        code,out,err=run_command([sys.executable,'-c',"import os; print('中文作答'); print(os.environ['PYTHONIOENCODING']); print(os.environ['PYTHONUTF8'])"],self.root)
        self.assertEqual(code,0);self.assertEqual(err,'')
        self.assertEqual(out.splitlines(),['中文作答','utf-8','1'])
    def test_inventory_nested_identities_and_records(self):
        def put(name,data):
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data),encoding='utf-8')
        put('runs/sub/submission.json',{'schema':'submission-receipt-v1','packet':{'answer':{'student_id':'s1','origin':'student'},'assignment':{'student_id':'s1'}},'evidence':{'student_id':'s1'},'learning_evidence_eligible':True})
        put('runs/fixture/submission.json',{'packet':{'answer':{'student_id':'s1','origin':'test_fixture'}},'learning_evidence_eligible':False})
        put('runs/student/verification.json',{'question_id':'p1'})
        put('runs/student/approval.json',{'question':{'student_evidence':{'student_id':'s1'}}})
        put('runs/gen/generation.json',{'question_id':'q1','student_id':'s2'})
        put('runs/reference/verification.json',{'question_id':'q1'})
        put('runs/reference/approval.json',{'question_id':'q1','question':{'reference_code':'class Main {}'}})
        put('artifacts/new/new-evidence.json',[{'student_id':'s1','question':'Q','program':'P'}])
        put('artifacts/old/train-evidence.json',[{'student_id':'s1','program':'P'}])
        data=self.service.bootstrap()
        submissions={x['label']:x for x in data['submission_runs']}
        self.assertEqual(submissions['sub']['student_id'],'s1')
        self.assertTrue(submissions['sub']['learning_evidence_eligible'])
        self.assertFalse(submissions['fixture']['learning_evidence_eligible'])
        verified={x['label']:x for x in data['verification_runs']}
        self.assertEqual((verified['student']['student_id'],verified['student']['verification_kind']),('s1','student'))
        self.assertEqual((verified['reference']['student_id'],verified['reference']['verification_kind']),('s2','reference'))
        self.assertEqual([x['path'] for x in data['record_files']],['artifacts/new/new-evidence.json'])
    def test_failure_and_redaction(self):
        self.service.runner=lambda a,c:(1,'Authorization: Bearer sk-private-value','api_key=private-value')
        job=self.wait(self.service.launch({'operation':'profile','mode':'preview','fields':{}}))
        self.assertEqual(job['status'],'failed')
        self.assertNotIn('private-value',json.dumps(job))
    def test_busy_rejected(self):
        gate=threading.Event()
        self.addCleanup(gate.set)
        self.service.runner=lambda a,c:(gate.wait(2) and 0,'','')
        job=self.service.launch({'operation':'profile','mode':'preview','fields':{}})
        with self.assertRaises(RuntimeError):self.service.launch({'operation':'profile','mode':'preview','fields':{}})
        gate.set();self.wait(job)
    def test_bootstrap(self):
        data=self.service.bootstrap()
        self.assertEqual(data['profiles'][0]['student_id'],'s1')
        self.assertIn('csrf_token',data)

    def test_command_modes_and_approval_are_explicit(self):
        cases = (
            ('profile', 'execute', '--execute'),
            ('submit', 'save', '--save'),
            ('verify_student', 'execute', '--execute'),
        )
        for operation, mode, flag in cases:
            with self.subTest(operation=operation):
                payload = dict(operation=operation, mode=mode, confirm=True, fields={})
                if operation.startswith('verify_'):
                    payload['approve_sha256'] = 'a' * 64
                args, output = self.service.command(payload, 'mode-test')
                self.assertEqual(args[-1], flag)
                self.assertEqual(output, 'runs/workbench-mode-test')
                self.assertNotIn('--save' if flag == '--execute' else '--execute', args)
                if operation.startswith('verify_'):
                    self.assertEqual(args[args.index('--approve-sha256') + 1], 'a' * 64)
        self.assertEqual(self.calls, [])

    def test_command_keeps_repeated_arguments_and_converts_run_file(self):
        folder = self.root / 'runs/previous'
        folder.mkdir()
        (folder / 'recommendation.json').write_text('{}')
        args, _ = self.service.command({
            'operation': 'generate', 'mode': 'preview',
            'fields': {
                'recommendation_run': 'runs/previous/recommendation.json',
                'requirement': ['保留中文', '--execute'],
                'targets': ['java.array', 'java.loop'],
            },
        }, 'arguments')
        self.assertEqual(args[args.index('--recommendation-run') + 1], str(folder))
        self.assertIn('--requirement=保留中文', args)
        self.assertIn('--requirement=--execute', args)
        self.assertNotIn('--execute', args)
        target_index = args.index('--targets')
        self.assertEqual(args[target_index + 1:target_index + 3], ['java.array', 'java.loop'])
        self.assertEqual(self.calls, [])

    def test_unknown_job_and_returned_job_cannot_change_queue(self):
        with self.assertRaises(KeyError):
            self.service.get_job('missing')
        job = self.wait(self.service.launch({
            'operation': 'profile', 'mode': 'preview', 'fields': {},
        }))
        job['status'] = 'tampered'
        self.service.jobs()[0]['status'] = 'tampered'
        self.assertEqual(self.service.get_job(job['id'])['status'], 'succeeded')

    def test_http_host_csrf_and_routes(self):
        server=make_server(self.root,0,self.service)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        self.addCleanup(conn.close)
        conn.request('GET','/api/bootstrap',headers={'Host':'evil.example'})
        response=conn.getresponse();self.assertEqual(response.status,403);response.read()
        conn.request('POST','/api/jobs',body='{}',headers={'Content-Type':'application/json'})
        response=conn.getresponse();self.assertEqual(response.status,403);response.read()
        conn.request('GET','/api/artifact?path=runs/x/profile.json')
        response=conn.getresponse();self.assertEqual(json.loads(response.read())['artifact']['student_id'],'s1')
        conn.request('POST','/api/jobs',body=json.dumps({'operation':'profile','mode':'preview','fields':{}}),headers={'Content-Type':'application/json','X-Workbench-Token':self.service.csrf_token})
        response=conn.getresponse();self.assertEqual(response.status,202);job=json.loads(response.read())['job'];self.wait(job)
    def test_finite_output_and_exception(self):
        self.service.runner=lambda a,c:(0,'x'*100000,'')
        job=self.wait(self.service.launch({'operation':'profile','mode':'preview','fields':{}}))
        self.assertLessEqual(len(job['log']),24000)
        def broken(a,c):raise RuntimeError('secret-value')
        self.service.runner=broken
        job=self.wait(self.service.launch({'operation':'profile','mode':'preview','fields':{}}))
        self.assertEqual(job['status'],'failed');self.assertNotIn('secret-value',job['error'])

if __name__=='__main__':unittest.main()
