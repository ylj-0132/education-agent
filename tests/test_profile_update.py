import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch
from profiling.api_client import CachedChatClient
from profiling.run_store import RunStore, RecordedTransport
from test_recommendation import catalog, profile
from test_recommendation_comparison import response
from profiling.incremental import run_update


class ProfileUpdateTests(unittest.TestCase):
    def test_cli_preview_without_credentials(self):
        for name, data in [('base', self.base), ('rows', [self.row]), ('catalog', catalog())]:
            (self.root/(name+'.json')).write_text(json.dumps(data), encoding='utf-8')
        result = subprocess.run([sys.executable, 'update_profile.py', '--base-profile', str(self.root/'base.json'),
            '--records', str(self.root/'rows.json'), '--catalog', str(self.root/'catalog.json'),
            '--env-file', str(self.root/'missing.env')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['mode'], 'preview')

    def setUp(self):
        t=tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup); self.root=Path(t.name)
        self.base=profile(); self.row=deepcopy(self.base['evidence'][0])
        self.row.update(record_id='new-evidence', problem_id='new-problem', program='NEW_PROGRAM')
        self.row['source']['index']=99

    def client(self,store,transport):
        return CachedChatClient(base_url='https://example.com/v1',api_key='fake',model='test',cache_dir=self.root/'cache',
            max_calls=12,transport=RecordedTransport(store,transport),event_sink=store.api_event)

    def transport(self,url,key,payload,timeout):
        text=payload['messages'][0]['content']
        if 'Extract concept' in text:
            self.assertIn('NEW_PROGRAM',text)
            return response({'observations':[{'concept_id':'a','status':'Good','description':'本次提交的具体代码表现'}]})
        self.assertIn('new-evidence',text);self.assertIn('e1',text)
        return response({'evaluation':'旧证据与新表现并存，不能据此判断全面掌握。'})

    def test_append_preserves_old_and_only_resummarizes_affected(self):
        before=deepcopy(self.base)
        with RunStore(self.root/'run',{'test':1},12) as store:
            result=run_update(store,self.client(store,self.transport),self.base,[self.row],catalog())
            self.assertEqual(set(store.state['steps']),{'extract:new-evidence','summarize:a'})
        self.assertEqual(self.base,before)
        p=result['profile'];self.assertEqual(p['evidence'][:-1],before['evidence'])
        self.assertEqual(p['observations'][:len(before['observations'])],before['observations'])
        self.assertIn('new-evidence',p['summaries']['a']['evidence_ids'])
        for k,v in before['summaries'].items():
            if k!='a':self.assertEqual(p['summaries'][k],v)
        with RunStore(self.root/'run',{'test':1},12,resume=True) as store:
            resumed=run_update(store,self.client(store,lambda *a:self.fail('network')),self.base,[self.row],catalog())
        self.assertEqual(resumed['profile'],p)

    def test_duplicate_and_cross_student_rejected_before_network(self):
        for row in [self.base['evidence'][0],dict(self.row,student_id='other'),dict(self.row,split='test')]:
            with tempfile.TemporaryDirectory() as tmp,RunStore(Path(tmp)/'run',{'test':1},12) as store:
                with self.assertRaises(ValueError):run_update(store,self.client(store,lambda *a:self.fail('network')),self.base,[row],catalog())

    def test_output_write_interruption_recovers_without_new_requests(self):
        with RunStore(self.root/'run',{'test':1},12) as store:
            with patch('profiling.incremental.atomic_json', side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    run_update(store,self.client(store,self.transport),self.base,[self.row],catalog())
            self.assertEqual(store.state['status'],'interrupted')
            count=len(store.state['attempts'])
        with RunStore(self.root/'run',{'test':1},12,resume=True) as store:
            result=run_update(store,self.client(store,lambda *a:self.fail('network')),self.base,[self.row],catalog())
            self.assertEqual(result['status'],'completed')
            self.assertEqual(len(store.state['attempts']),count)

    @patch('time.sleep')
    def test_summary_failure_does_not_publish_updated_profile(self,sleep):
        def transport(url,key,payload,timeout):
            if 'Extract concept' in payload['messages'][0]['content']:return self.transport(url,key,payload,timeout)
            return response({'evaluation':''})
        with RunStore(self.root/'run',{'test':1},12) as store:
            result=run_update(store,self.client(store,transport),self.base,[self.row],catalog())
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(result['profile'])
        self.assertFalse((self.root/'run/profile.json').exists())
