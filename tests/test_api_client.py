import json
import tempfile
import unittest
import subprocess
import sys
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.error import URLError
from unittest.mock import patch
from profiling.api_client import CachedChatClient, read_env


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.calls=[]
        def transport(url,key,payload,timeout):
            self.calls.append(payload)
            return {'choices':[{'finish_reason':'stop','message':{'content':'{"evaluation":"ok"}'}}],
                    'usage':{'total_tokens':42}}
        self.transport=transport

    def client(self,**kwargs):
        return CachedChatClient(base_url='https://example.com/v1',api_key='secret',model=kwargs.pop('model','test'),
                                cache_dir=self.root/'cache',transport=self.transport,**kwargs)

    def test_cache_and_model_isolation(self):
        a=self.client();a.complete('summarize','prompt');a.complete('summarize','prompt')
        self.assertEqual(len(self.calls),1);self.assertEqual(a.events[1]['cache_hit'],True)
        self.client(model='other').complete('summarize','prompt')
        self.assertEqual(len(self.calls),2)
        self.assertNotIn('secret',''.join(p.read_text() for p in (self.root/'cache').glob('*.json')))

    def test_call_limit(self):
        a=self.client(max_calls=1);a.complete('summarize','a')
        with self.assertRaisesRegex(RuntimeError,'limit'):a.complete('summarize','b')
        self.assertEqual(len(self.calls),1)

    def test_luna_completion_budget(self):
        self.client(model='gpt-5.6-luna').complete('summarize','prompt')
        self.assertNotIn('max_tokens',self.calls[0])
        self.assertEqual(self.calls[0]['max_completion_tokens'],1500)
        self.assertEqual(self.calls[0]['reasoning_effort'],'low')

    def test_invalid_response_not_cached(self):
        self.transport=lambda *a: {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}
        a=self.client()
        with self.assertRaises(ValueError):a.complete('summarize','a')
        self.assertEqual(list((self.root/'cache').glob('*.json')),[])
        self.assertEqual(a.events[0]['finish_reason'],'length')
        self.assertTrue((self.root/'cache'/'failures').exists())

    def test_error_redaction(self):
        def fail(*a):raise OSError('secret provider detail')
        self.transport=fail;a=self.client()
        with self.assertRaises(RuntimeError) as caught:a.complete('summarize','a')
        self.assertNotIn('secret',str(caught.exception));self.assertEqual(a.events[0]['status'],'failed')

    def test_env_and_required_values(self):
        p=self.root/'.env';p.write_text('# comment\nZZZ_API_KEY="abc=def"\nZZZ_MODEL=\n')
        self.assertEqual(read_env(p)['ZZZ_API_KEY'],'abc=def')
        with self.assertRaises(ValueError):self.client(model='')

    def test_plain_json_fence(self):
        self.transport=lambda *a: {'choices':[{'finish_reason':'stop','message':{'content':'```json\n{"evaluation":"ok"}\n```'}}]}
        self.assertEqual(self.client().complete('summarize','p')['evaluation'],'ok')

    def test_preview_needs_no_env_and_writes_nothing(self):
        records=self.root/'records.json';concepts=self.root/'concepts.json';out=self.root/'run'
        records.write_text(json.dumps([{'student_id':'a','split':'train','problem_id':'p1'}]))
        concepts.write_text(json.dumps({'version':'test','concepts':{'loop':'Loop'}}))
        run=subprocess.run([sys.executable,'run_profile.py','--records',str(records),
            '--concepts',str(concepts),'--student','a','--env-file',str(self.root/'absent.env'),
            '--output',str(out)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout)['mode'],'preview')
        self.assertFalse(out.exists())

    @patch('time.sleep')
    def test_retry_invalid_then_success_preserves_failure(self, sleep):
        good=self.transport
        responses=iter([{'choices':[{'finish_reason':'stop','message':{'content':'unexpected refusal'}}]},
                        good(None,None,{},None)])
        self.transport=lambda *a: next(responses)
        client=self.client()
        self.assertEqual(client.complete('summarize','p')['evaluation'],'ok')
        self.assertEqual(client.calls,2)
        self.assertEqual([e['status'] for e in client.events],['failed','ok'])
        self.assertEqual(len(list((self.root/'cache'/'failures').glob('*.json'))),1)
        client.complete('summarize','p')
        self.assertEqual(client.calls,2)
        sleep.assert_called_once_with(2)

    @patch('time.sleep')
    def test_retry_stops_after_two_and_keeps_both_failures(self, sleep):
        self.transport=lambda *a: {'choices':[{'finish_reason':'stop','message':{'content':'bad'}}]}
        client=self.client()
        with self.assertRaises(ValueError):client.complete('summarize','p')
        self.assertEqual(client.calls,2)
        self.assertEqual(len(list((self.root/'cache'/'failures').glob('*.json'))),2)

    @patch('time.sleep')
    def test_retry_respects_total_budget(self, sleep):
        self.transport=lambda *a: {'choices':[{'finish_reason':'stop','message':{'content':'bad'}}]}
        client=self.client(max_calls=1)
        with self.assertRaises(ValueError):client.complete('summarize','p')
        self.assertEqual(client.calls,1)
        sleep.assert_not_called()

    @patch('time.sleep')
    def test_timeout_and_gateway_retry(self, sleep):
        for failure in (TimeoutError(),HTTPError('https://example.com',503,'temporary',{},None)):
            with self.subTest(failure=type(failure).__name__):
                good=self.transport
                attempts=[]
                def transport(*args):
                    attempts.append(1)
                    if len(attempts)==1:raise failure
                    return {'choices':[{'finish_reason':'stop','message':{'content':'{"evaluation":"ok"}'}}]}
                self.transport=transport
                client=self.client(model=str(type(failure)))
                self.assertEqual(client.complete('summarize','p')['evaluation'],'ok')
                self.assertEqual(client.calls,2)

    @patch('time.sleep')
    def test_account_errors_and_truncation_do_not_retry(self, sleep):
        for code in (400,401,403,402,429):
            def transport(*a):raise HTTPError('https://example.com',code,'secret',{},None)
            self.transport=transport;client=self.client()
            with self.assertRaises(RuntimeError):client.complete('summarize','p')
            self.assertEqual(client.calls,1)
        for reason in ('length','content_filter'):
            self.transport=lambda *a: {'choices':[{'finish_reason':reason,'message':{'content':'{}'}}]}
            client=self.client()
            with self.assertRaises(ValueError):client.complete('summarize','p')
            self.assertEqual(client.calls,1)
        sleep.assert_not_called()

    @patch('time.sleep')
    def test_refusal_inside_evaluation_is_rejected(self,sleep):
        self.transport=lambda *a: {'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps({'evaluation':'抱歉，本服务不支持以俄语提出的请求。'})}}]}
        client=self.client()
        with self.assertRaises(ValueError):client.complete('summarize','p')
        self.assertEqual(client.calls,2)
        self.assertFalse(list((self.root/'cache').glob('*.json')))

    def test_old_cached_refusal_is_quarantined(self):
        client=self.client();client.complete('summarize','p')
        cache_file=next((self.root/'cache').glob('*.json'))
        data=json.loads(cache_file.read_text(encoding='utf-8'))
        data['parsed']={'evaluation':'抱歉，本服务不支持以俄语提出的请求。'}
        cache_file.write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual(client.complete('summarize','p')['evaluation'],'ok')
        self.assertEqual(client.calls,2)
        self.assertTrue(list((self.root/'cache'/'failures').glob('*cached*')))

    def test_uncertain_assessment_is_not_refusal(self):
        self.transport=lambda *a: {'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps({'evaluation':'证据不足，无法判断是否掌握循环。'})}}]}
        self.assertIn('证据不足',self.client().complete('summarize','p')['evaluation'])

    @patch('time.sleep')
    def test_ssl_eof_retries_but_certificate_errors_do_not(self,sleep):
        calls=[]
        def transport(*args):
            calls.append(1)
            if len(calls)==1:raise URLError(ssl.SSLEOFError(8,'unexpected EOF'))
            return {'choices':[{'finish_reason':'stop','message':{'content':'{"evaluation":"ok"}'}}]}
        self.transport=transport
        client=self.client()
        self.assertEqual(client.complete('summarize','eof')['evaluation'],'ok')
        self.assertEqual(client.calls,2)
        for exc in (ssl.SSLCertVerificationError(1,'certificate verify failed'),ssl.SSLError(1,'unsupported protocol')):
            def fail(*args):raise URLError(exc)
            self.transport=fail;client=self.client()
            with self.assertRaises(RuntimeError):client.complete('summarize','cert')
            self.assertEqual(client.calls,1)
