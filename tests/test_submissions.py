from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from profiling.api_client import CachedChatClient
from profiling.run_store import RunStore, RecordedTransport, fingerprint
from profiling.incremental import run_update
from recommendation.context import prepare_context
from generation.workflow import run_generation
from submissions.workflow import prepare_submission, receive_submission, load_submission_evidence
from test_generation import request, question, review
from test_recommendation import profile, catalog
from test_recommendation_comparison import response


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        outputs=iter([question(),review(question())])
        with RunStore(self.root/'generation',{'test':1},8) as store:
            self.generated=run_generation(store,self.client(store,lambda *a:response(next(outputs))),request())
        self.answer={'schema':'student-answer-v1','submission_id':'answer-1','student_id':'s1',
            'question_id':self.generated['question_id'],'question_sha256':fingerprint(question()),
            'origin':'student','program':'STUDENT_ANSWER_SENTINEL','submitted_at':None}

    def client(self,store,transport):
        return CachedChatClient(base_url='https://example.com/v1',api_key='fake',model='test',cache_dir=self.root/'cache',
            max_calls=8,transport=RecordedTransport(store,transport),event_sink=store.api_event)

    def save(self,answer=None):
        packet=prepare_submission(self.root/'generation',answer or self.answer)
        with RunStore(self.root/'submission',{'packet_sha256':fingerprint(packet)},0) as store:
            receipt=receive_submission(store,packet)
        return packet,receipt

    def test_binding_and_no_reference_answer_in_learning_input(self):
        packet,receipt=self.save();row=load_submission_evidence(self.root/'submission')
        self.assertEqual(row['split'],'live')
        self.assertEqual(row['program'],'STUDENT_ANSWER_SENTINEL')
        self.assertNotIn(question()['reference_code'],row['question'])
        self.assertEqual(row['submission']['question_sha256'],fingerprint(question()))
        self.assertEqual(row['submission']['submitted_at'],None)
        self.assertTrue(row['submission']['received_at'])
        with RunStore(self.root/'submission',{'packet_sha256':fingerprint(packet)},0,resume=True) as store:
            self.assertEqual(receive_submission(store,packet),receipt)
            self.assertEqual(store.state['attempts'],[])
        for key,value in [('student_id','other'),('question_id','wrong'),('question_sha256','wrong'),('submitted_at','not a date')]:
            with self.assertRaises(ValueError):prepare_submission(self.root/'generation',dict(self.answer,**{key:value}))

    def test_test_fixture_cannot_update_profile_even_if_relabelled_train(self):
        self.save(dict(self.answer,origin='test_fixture'))
        with self.assertRaises(ValueError):load_submission_evidence(self.root/'submission')
        from profiling.student_profile import StudentProfile
        row=deepcopy(profile()['evidence'][0]);row['evidence_origin']='test_fixture'
        builder=StudentProfile('s1',catalog()['concepts'],lambda *a:self.fail('model'),model_id='fake')
        with self.assertRaises(ValueError):builder.insert([row])
        p=profile();p['evidence'][0]=row
        with self.assertRaises(ValueError):prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'],profile=p)

    def test_receipt_tampering_and_duplicate_update_rejected(self):
        self.save();row=load_submission_evidence(self.root/'submission')
        base=profile();base['evidence'].append(row)
        with RunStore(self.root/'update',{'test':1},8) as store:
            with self.assertRaises(ValueError):run_update(store,self.client(store,lambda *a:self.fail('network')),base,[row],catalog())
        p=self.root/'submission/submission.json';r=json.loads(p.read_text('utf-8'));r['evidence']['program']='changed';p.write_text(json.dumps(r),encoding='utf-8')
        with self.assertRaises(ValueError):load_submission_evidence(self.root/'submission')

    def test_changed_generation_request_and_resume_payload_rejected(self):
        packet,_=self.save()
        changed=deepcopy(packet);changed['answer']['program']='OTHER'
        with self.assertRaises(ValueError):
            with RunStore(self.root/'submission',{'packet_sha256':fingerprint(changed)},0,resume=True):pass
        p=self.root/'generation/request.json';req=json.loads(p.read_text('utf-8'))
        req['student_id']='other';p.write_text(json.dumps(req),encoding='utf-8')
        with self.assertRaises(ValueError):prepare_submission(self.root/'generation',self.answer)

    def test_live_answer_updates_isolated_profile_and_recommendation_context(self):
        self.save();row=load_submission_evidence(self.root/'submission');base=profile();before=deepcopy(base)
        def transport(url,key,payload,timeout):
            prompt=payload['messages'][0]['content']
            self.assertNotIn(question()['reference_code'],prompt)
            if 'Extract concept' in prompt:
                self.assertIn('STUDENT_ANSWER_SENTINEL',prompt)
                return response({'observations':[{'concept_id':'a','status':'Unknown','description':'测试替身，尚无运行证据'}]})
            return response({'evaluation':'仅测试回流连接，不作真实学习结论'})
        with RunStore(self.root/'update',{'test':1},8) as store:
            result=run_update(store,self.client(store,transport),base,[row],catalog())
        self.assertEqual(base,before)
        ctx=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'],profile=result['profile'])
        self.assertIn(row['record_id'],[r['record_id'] for r in ctx['evidence']])
        self.assertEqual(ctx['evidence'][-1]['submission']['question_id'],self.answer['question_id'])
        from recommendation.runner import run_recommendation
        from test_recommendation_comparison import decision
        with RunStore(self.root/'recommendation',{'test':1},8) as store:
            d=dict(decision(),evidence_ids=[row['record_id']])
            rec=run_recommendation(store,self.client(store,lambda *a:response(d)),ctx)
        self.assertEqual(rec['status'],'recommended')
        self.assertIn(row['record_id'],[r['record_id'] for r in rec['generation_request']['evidence']])
        with RunStore(self.root/'update',{'test':1},8,resume=True) as store:
            resumed=run_update(store,self.client(store,lambda *a:self.fail('network')),base,[row],catalog())
        self.assertEqual(resumed['profile'],result['profile'])

    def test_received_answer_recovers_after_export_failure(self):
        packet=prepare_submission(self.root/'generation',self.answer)
        with RunStore(self.root/'submission',{'packet_sha256':fingerprint(packet)},0) as store:
            with patch('submissions.workflow.atomic_json',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):receive_submission(store,packet)
            self.assertEqual(store.state['status'],'interrupted')
        with RunStore(self.root/'submission',{'packet_sha256':fingerprint(packet)},0,resume=True) as store:
            receive_submission(store,packet)
        self.assertEqual(load_submission_evidence(self.root/'submission')['program'],self.answer['program'])

    def test_cli_preview_save_and_update_preview_without_credentials(self):
        for name,value in [('answer',self.answer),('profile',profile()),('catalog',catalog())]:
            (self.root/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
        args=[sys.executable,'submit_answer.py','--generation-run',str(self.root/'generation'),
              '--submission',str(self.root/'answer.json'),'--output',str(self.root/'submission')]
        r=subprocess.run(args,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse((self.root/'submission').exists())
        r=subprocess.run(args+['--save'],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        r=subprocess.run([sys.executable,'update_profile.py','--base-profile',str(self.root/'profile.json'),
            '--catalog',str(self.root/'catalog.json'),'--submission-run',str(self.root/'submission'),
            '--env-file',str(self.root/'missing.env')],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
        (self.root/'fake.env').write_text('ZZZ_BASE_URL=https://example.com/v1\nZZZ_API_KEY=fake\nZZZ_MODEL=test\n',encoding='utf-8')
        bootstrap='''import runpy,json
import profiling.api_client as api
def fake(*args):
 return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'observations':[]})}}]}
api.send_chat=fake
runpy.run_path('update_profile.py',run_name='__main__')
'''
        args=[sys.executable,'-c',bootstrap,'--base-profile',str(self.root/'profile.json'),
            '--catalog',str(self.root/'catalog.json'),'--submission-run',str(self.root/'submission'),
            '--env-file',str(self.root/'fake.env'),'--output',str(self.root/'updated'),
            '--cache',str(self.root/'update-cache'),'--execute']
        r=subprocess.run(args,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        updated=json.loads((self.root/'updated/profile.json').read_text('utf-8'))
        self.assertEqual(updated['evidence'][-1]['split'],'live')
        self.assertEqual(updated['evidence'][-1]['program'],self.answer['program'])
