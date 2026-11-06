import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from profiling.api_client import CachedChatClient
from profiling.student_profile import StudentProfile
from profiling.run_store import RunStore, RecordedTransport
from profiling.reliable_runner import run_profile


def reply(value):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}


def observation(concept='known'):
    return {'observations':[{'concept_id':concept,'status':'Good','description':'能按要求读取中文题干中的整数。'}]}


def record():
    return {'record_id':'r1','student_id':'s1','split':'train','problem_id':'p1',
            'question':'读取一个整数','program':'CODE','source':{'file':'offline'},
            'annotations':{'desc':'D','error_desc':'E'}}


class BusinessCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def client(self,transport,store=None):
        return CachedChatClient(base_url='https://example.com/v1',api_key='offline-secret',model='test',
            cache_dir=self.root/'cache',transport=RecordedTransport(store,transport) if store else transport,
            event_sink=store.api_event if store else None,max_calls=store.max_calls if store else 8)

    def builder(self,client):
        return StudentProfile('s1',{'known':'整数输入'},client.complete,model_id='test')

    @patch('time.sleep')
    def test_unknown_concept_never_enters_success_cache(self,sleep):
        client=self.client(lambda *a:reply(observation('invented')))
        with self.assertRaises(ValueError):self.builder(client).insert([record()])
        self.assertFalse(list((self.root/'cache').glob('*.json')))
        failures=list((self.root/'cache'/'failures').glob('*.json'))
        self.assertEqual(len(failures),2)
        self.assertEqual(client.calls,2)
        data=json.loads(failures[0].read_text(encoding='utf-8'))
        self.assertEqual(data['validation_error'],'business_validation')
        self.assertEqual(json.loads(data['response']['choices'][0]['message']['content']),observation('invented'))

    def test_legacy_business_invalid_cache_is_quarantined_and_replaced(self):
        prompts=[]
        def capture(stage,prompt):prompts.append(prompt);return observation()
        StudentProfile('s1',{'known':'整数输入'},capture,model_id='test').insert([record()])
        client=self.client(lambda *a:reply(observation('invented')))
        client.complete('extract',prompts[0])  # Simulate a cache created before business validation.
        client.transport=lambda *a:reply(observation())
        result=self.builder(client).insert([record()])
        self.assertEqual(result[0]['concept_id'],'known')
        self.assertEqual(client.calls,2)
        self.assertTrue(list((self.root/'cache'/'failures').glob('*cached*')))
        self.assertTrue(any(e['status']=='cache_rejected' for e in client.events))

    @patch('time.sleep')
    def test_duplicate_concepts_are_quarantined(self,sleep):
        value=observation();value['observations']*=2
        client=self.client(lambda *a:reply(value))
        with self.assertRaises(ValueError):self.builder(client).insert([record()])
        self.assertFalse(list((self.root/'cache').glob('*.json')))
        self.assertEqual(client.calls,2)

    def test_chinese_and_unknown_are_valid_and_reusable(self):
        value=observation();value['observations'][0]['status']='Unknown'
        client=self.client(lambda *a:reply(value));builder=self.builder(client)
        self.assertEqual(builder.insert([record()])[0]['status'],'Unknown')
        client.transport=lambda *a:self.fail('valid cache should be reused')
        self.assertEqual(builder.insert([record()])[0]['status'],'Unknown')
        self.assertEqual(client.calls,1)

    @patch('time.sleep')
    def test_resume_does_not_replay_rejected_response_or_reset_limits(self,sleep):
        calls=[]
        def invalid(*a):calls.append(1);return reply(observation('invented'))
        with RunStore(self.root/'run',{'v':1},2) as store:
            result=run_profile(store,self.client(invalid,store),[record()],{'known':'整数输入'},'test')
            self.assertEqual(result['status'],'partial')
            self.assertTrue(all(a['status']=='rejected' for a in store.snapshot()['attempts']))
        with RunStore(self.root/'run',{'v':1},2,resume=True) as store:
            result=run_profile(store,self.client(lambda *a:self.fail('budget reset'),store),[record()],{'known':'整数输入'},'test')
            self.assertEqual(result['status'],'partial')
            self.assertEqual(store.snapshot()['requests_reserved'],2)
        self.assertEqual(len(calls),2)

    @patch('time.sleep')
    def test_received_response_is_business_validated_after_crash(self,sleep):
        identity={'v':1};prompts=[]
        def capture(stage,prompt):prompts.append(prompt);return observation()
        StudentProfile('s1',{'known':'整数输入'},capture,model_id='test').insert([record()])
        with RunStore(self.root/'run',identity,2) as store:
            store.start_step('extract:r1',{})
            transport=RecordedTransport(store,lambda *a:reply(observation('invented')))
            client=self.client(lambda *a:self.fail('unused'),store)
            def crash(*args):transport(*args);raise KeyboardInterrupt()
            client.transport=crash
            with self.assertRaises(KeyboardInterrupt):client.complete('extract',prompts[0])
        with RunStore(self.root/'run',identity,2,resume=True) as store:
            store.start_step('extract:r1',{})
            client=self.client(lambda *a:reply(observation()),store)
            self.assertEqual(self.builder(client).insert([record()])[0]['concept_id'],'known')
            self.assertEqual([a['status'] for a in store.snapshot()['attempts']],['rejected','accepted'])
            self.assertEqual(store.snapshot()['requests_reserved'],2)

    def test_corrupt_cache_is_preserved_and_not_reused(self):
        client=self.client(lambda *a:reply({'evaluation':'中文评价'}))
        client.complete('summarize','p')
        cache=next((self.root/'cache').glob('*.json'));cache.write_text('{broken',encoding='utf-8')
        self.assertEqual(client.complete('summarize','p')['evaluation'],'中文评价')
        self.assertEqual(client.calls,2)
        self.assertEqual(next((self.root/'cache'/'failures').glob('*cached*')).read_text(encoding='utf-8'),'{broken')
