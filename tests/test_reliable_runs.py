import json
import tempfile
import unittest
import subprocess
import sys
import ssl
from pathlib import Path
from unittest.mock import patch

from profiling.run_store import RunStore, RecordedTransport
from profiling.api_client import CachedChatClient
from profiling.reliable_runner import run_profile


def response(content):
    return {'id':'provider-1','choices':[{'finish_reason':'stop','message':{'content':json.dumps(content)}}],
            'usage':{'total_tokens':10}}


def record():
    return {'record_id':'r1','student_id':'s1','split':'train','problem_id':'p1',
            'question':'Q','program':'CODE','annotations':{'desc':'D','error_desc':'E'},'source':{'file':'f','index':0}}


class ReliableTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.identity={'input':'v1'}

    def store(self,resume=False,budget=8):
        return RunStore(self.root/'run',self.identity,budget,resume=resume,secret='secret')

    def client(self,store,transport):
        return CachedChatClient(base_url='https://example.com/v1',api_key='secret',model='test',
            cache_dir=self.root/'cache',max_calls=store.max_calls,
            transport=RecordedTransport(store,transport),event_sink=store.api_event)

    def test_interrupted_request_is_unknown_and_not_resent(self):
        def crash(*args):raise KeyboardInterrupt()
        with self.store() as s:
            s.start_step('summarize:loops',{})
            with self.assertRaises(KeyboardInterrupt):self.client(s,crash).complete('summarize','prompt')
        with self.store(resume=True) as s:
            s.start_step('summarize:loops',{})
            with self.assertRaises(RuntimeError):self.client(s,lambda *a:self.fail('resent')).complete('summarize','prompt')
            self.assertEqual(s.snapshot()['requests_reserved'],1)
            self.assertEqual(s.snapshot()['attempts'][0]['status'],'unknown')

    def test_received_response_survives_before_validation(self):
        with self.store() as s:
            s.start_step('summarize:loops',{})
            transport=RecordedTransport(s,lambda *a:response({'evaluation':'ok'}))
            client=self.client(s,lambda *a:self.fail('unused'))
            # Die after transport durably receives response, before adapter validation.
            def crash(*args):
                transport(*args)
                raise KeyboardInterrupt()
            client.transport=crash
            with self.assertRaises(KeyboardInterrupt):client.complete('summarize','prompt')
        with self.store(resume=True) as s:
            s.start_step('summarize:loops',{})
            self.assertEqual(self.client(s,lambda *a:self.fail('resent')).complete('summarize','prompt'),{'evaluation':'ok'})
            self.assertEqual(s.snapshot()['requests_reserved'],1)

    @patch('time.sleep')
    def test_failed_summary_does_not_block_others_or_reset_retries(self,sleep):
        calls=[]
        def transport(url,key,payload,timeout):
            prompt=payload['messages'][0]['content'];calls.append(prompt)
            if 'Extract concept performance' in prompt:
                return response({'observations':[{'concept_id':k,'status':'Good','description':'evidence'} for k in ('a','b')]})
            if 'Knowledge Point: A' in prompt:return response({'wrong':'bad'})
            return response({'evaluation':'ok'})
        with self.store() as s:
            result=run_profile(s,self.client(s,transport),[record()],{'a':'A','b':'B'},'test')
            self.assertEqual(result['status'],'partial')
            self.assertEqual(set(result['summaries']),{'b'})
            self.assertFalse((self.root/'run'/'profile.json').exists())
        count=len(calls)
        with self.store(resume=True) as s:
            result=run_profile(s,self.client(s,transport),[record()],{'a':'A','b':'B'},'test')
            self.assertEqual(result['status'],'partial')
            self.assertEqual(len(calls),count)
            self.assertEqual(s.snapshot()['requests_reserved'],4)

    def test_budget_identity_and_lock(self):
        with self.store(budget=1) as s:
            with self.assertRaises(RuntimeError):self.store(resume=True,budget=1)
            s.start_step('one',{})
            self.client(s,lambda *a:response({'evaluation':'ok'})).complete('summarize','one')
        with self.assertRaises(ValueError):RunStore(self.root/'run',{'input':'changed'},1,resume=True)
        with self.assertRaises(ValueError):self.store(resume=True,budget=2)
        with self.store(resume=True,budget=1) as s:
            s.start_step('two',{})
            with self.assertRaises(RuntimeError):self.client(s,lambda *a:self.fail('over budget')).complete('summarize','two')
            self.assertEqual(s.snapshot()['requests_reserved'],1)

    def test_completed_steps_resume_without_cache_or_network(self):
        def transport(url,key,payload,timeout):
            if 'Extract concept performance' in payload['messages'][0]['content']:
                return response({'observations':[{'concept_id':'a','status':'Good','description':'evidence'}]})
            return response({'evaluation':'ok'})
        with self.store() as s:
            self.assertEqual(run_profile(s,self.client(s,transport),[record()],{'a':'A'},'test')['status'],'completed')
        for p in (self.root/'cache').glob('*.json'):p.unlink()
        with self.store(resume=True) as s:
            self.assertEqual(run_profile(s,self.client(s,lambda *a:self.fail('resent')),[record()],{'a':'A'},'test')['status'],'completed')
            self.assertEqual(s.snapshot()['requests_reserved'],2)

    def test_error_trace_is_durable_and_redacted(self):
        def fail(*a):raise OSError('network secret')
        with self.store() as s:
            s.start_step('one',{'concept_id':'a'})
            with self.assertRaises(RuntimeError):self.client(s,fail).complete('summarize','p')
            trace=json.dumps(s.snapshot(),ensure_ascii=False)
            self.assertNotIn('secret',trace)
            self.assertIn('OSError',trace)
            self.assertIn('[REDACTED]',trace)
            self.assertTrue(all(e.get('time') for e in s.snapshot()['events']))

    def test_real_process_exit_releases_lock_and_keeps_reservation(self):
        code='''import os,sys
from profiling.run_store import RunStore, RecordedTransport
s=RunStore(sys.argv[1],{'input':'v1'},8,secret='secret')
s.start_step('one',{})
RecordedTransport(s,lambda *a:os._exit(23))('https://example.com','secret',{},60)
'''
        result=subprocess.run([sys.executable,'-c',code,str(self.root/'run')],capture_output=True)
        self.assertEqual(result.returncode,23)
        with self.store(resume=True) as s:
            self.assertEqual(s.snapshot()['attempts'][0]['status'],'unknown')
            self.assertEqual(s.snapshot()['requests_reserved'],1)

    def test_status_cli_without_env_or_input(self):
        with self.store() as s:s.start_step('one',{'concept_id':'a'})
        result=subprocess.run([sys.executable,'run_profile.py','--status','--output',str(self.root/'run'),
            '--env-file',str(self.root/'absent.env'),'--records',str(self.root/'absent.json')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['steps']['one']['status'],'running')
        result=subprocess.run([sys.executable,'run_profile.py','--trace','--output',str(self.root/'run')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['events'][-1]['event'],'step_started')

    def test_auth_error_stops_other_paid_calls(self):
        from urllib.error import HTTPError
        def fail(*a):raise HTTPError('https://example.com',401,'unauthorized',{},None)
        with self.store() as s:
            s.start_step('one',{})
            with self.assertRaises(RuntimeError):self.client(s,fail).complete('summarize','one')
            s.start_step('two',{})
            with self.assertRaises(RuntimeError):self.client(s,lambda *a:self.fail('account error resent')).complete('summarize','two')
            self.assertEqual(s.snapshot()['requests_reserved'],1)

    def test_interrupted_runner_has_pending_steps_and_no_false_profile(self):
        r2=record()|{'record_id':'r2','problem_id':'p2'}
        def crash(*a):raise KeyboardInterrupt()
        with self.store() as s:
            with self.assertRaises(KeyboardInterrupt):run_profile(s,self.client(s,crash),[record(),r2],{'a':'A'},'test')
            self.assertEqual(s.snapshot()['steps']['extract:r2']['status'],'pending')
            self.assertEqual(s.snapshot()['status'],'interrupted')
        self.assertFalse((self.root/'run'/'profile.json').exists())

    def test_business_validation_failure_does_not_become_checkpoint(self):
        with self.store() as s:
            result=run_profile(s,self.client(s,lambda *a:response({'observations':[
                {'concept_id':'invented','status':'Good','description':'bad'}]})),[record()],{'a':'A'},'test')
            self.assertEqual(result['status'],'partial')
            self.assertEqual(s.snapshot()['steps']['extract:r1']['status'],'failed')
            self.assertEqual(result['summaries'],{})

    def test_cli_execution_and_resume_without_real_api(self):
        records=self.root/'records.json';concepts=self.root/'concepts.json';env=self.root/'config.env'
        records.write_text(json.dumps([record()]),encoding='utf-8')
        concepts.write_text(json.dumps({'version':'test','concepts':{'a':'A'}}),encoding='utf-8')
        env.write_text('ZZZ_BASE_URL=https://example.com/v1\nZZZ_API_KEY=secret\nZZZ_MODEL=test\n',encoding='utf-8')
        bootstrap='''import runpy,json
import profiling.api_client as api
def fake(url,key,payload,timeout):
    text=payload['messages'][0]['content']
    value={'observations':[{'concept_id':'a','status':'Good','description':'evidence'}]} if 'Extract concept performance' in text else {'evaluation':'ok'}
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
api.send_chat=fake
runpy.run_path('run_profile.py',run_name='__main__')
'''
        args=['--execute','--records',str(records),'--concepts',str(concepts),'--student','s1',
              '--env-file',str(env),'--output',str(self.root/'run'),'--cache',str(self.root/'cache'),'--max-calls','2']
        first=subprocess.run([sys.executable,'-c',bootstrap,*args],capture_output=True,text=True)
        self.assertEqual(first.returncode,0,first.stderr)
        blocked=bootstrap.replace('api.send_chat=fake',"api.send_chat=lambda *a: (_ for _ in ()).throw(AssertionError('unexpected API'))")
        for p in (self.root/'cache').glob('*.json'):p.unlink()
        second=subprocess.run([sys.executable,'-c',blocked,*args,'--resume'],capture_output=True,text=True)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertEqual(RunStore.inspect(self.root/'run')['requests_reserved'],2)

    @patch('time.sleep')
    def test_ssl_eof_retry_policy_matches_checkpoint(self,sleep):
        from urllib.error import URLError
        calls=[]
        def transport(*a):
            calls.append(1)
            if len(calls)==1:raise URLError(ssl.SSLEOFError(8,'unexpected EOF'))
            return response({'evaluation':'ok'})
        with self.store() as s:
            s.start_step('one',{})
            self.assertEqual(self.client(s,transport).complete('summarize','p'),{'evaluation':'ok'})
            self.assertEqual(s.snapshot()['requests_reserved'],2)
            self.assertTrue(s.snapshot()['attempts'][0]['retryable'])
            self.assertEqual(s.snapshot()['attempts'][1]['status'],'accepted')
