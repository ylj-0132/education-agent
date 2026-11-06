import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from java5_data import load_records
from profiling.run_store import RunStore, fingerprint


class FullBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        for student in ('java_a','java_b'):
            folder = self.source/student
            folder.mkdir(parents=True)
            for split in ('train','test'):
                rows = [dict(problem_id=split+'1',question='Add two numbers',program='original code',
                             desc='external desc',error_desc='external error')]
                (folder/(split+'.json')).write_text(json.dumps(rows),encoding='utf-8')
        self.records = load_records(self.source)
        self.manifest = {'source_files':{r['source']['file']:r['source']['sha256'] for r in self.records}}
        self.manifest_path = self.root/'manifest.json'
        self.manifest_path.write_text(json.dumps(self.manifest),encoding='utf-8')

    def api(self):
        from profiling import dataset_store
        return dataset_store

    def test_source_hashes_checked_before_import(self):
        api=self.api()
        self.assertEqual(len(api.verified_records(self.source,self.manifest_path)),4)
        path=self.source/'java_a/train.json'
        path.write_text(path.read_text()+' ',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'source'):
            api.verified_records(self.source,self.manifest_path)

    def test_import_is_idempotent_and_test_is_separate(self):
        api=self.api()
        path=self.root/'dataset.sqlite3'
        for _ in range(2):
            with api.DatasetStore(path,self.records) as db:
                snap=db.snapshot()
                self.assertEqual(snap['records'],4)
                self.assertEqual(snap['splits'],{'test':2,'train':2})
                self.assertEqual(snap['completed_students'],[])
                self.assertEqual(db.db.execute('select count(*) from evidence where split=?',('test',)).fetchone()[0],2)
        changed=copy.deepcopy(self.records);changed[0]['program']='changed'
        with self.assertRaisesRegex(ValueError,'dataset'):
            api.DatasetStore(path,changed)

    def profile(self):
        rows=[r for r in self.records if r['student_id']=='java_a' and r['split']=='train']
        return dict(status='completed',student_id='java_a',evidence=rows,observations=[],summaries={},concepts={'java.input':'Input'})

    def completed_run(self,profile):
        run=self.root/'run'
        with RunStore(run,{'records_sha256':fingerprint(profile['evidence'])},2) as store:
            store.state['status']='completed';store.commit('done')
        (run/'profile.json').write_text(json.dumps(profile),encoding='utf-8')
        return run

    def test_publish_requires_complete_exact_train_and_checkpoint(self):
        api=self.api();profile=self.profile()
        with api.DatasetStore(self.root/'db.sqlite3',self.records) as db:
            for bad in [dict(profile,status='partial'),dict(profile,evidence=[]),dict(profile,evidence=self.records)]:
                with self.assertRaises(ValueError): db.publish(bad,self.root/'missing')
            with self.assertRaises((ValueError,OSError)): db.publish(profile,self.root/'missing')
            run=self.completed_run(profile)
            db.publish(profile,run);db.publish(profile,run)
            self.assertEqual(db.snapshot()['completed_students'],['java_a'])
            self.assertEqual(db.db.execute('select count(*) from profiles').fetchone()[0],1)
            bad=copy.deepcopy(profile);bad['observations']=[{'evidence_id':'not-in-train','student_id':'java_a','concept_id':'java.input'}]
            with self.assertRaises(ValueError): db.publish(bad,run)

    def test_preview_no_env_no_writes(self):
        script=Path(__file__).resolve().parents[1]/'run_java5_batch.py'
        result=subprocess.run([sys.executable,str(script),'--data-root',str(self.source),
            '--source-manifest',str(self.manifest_path),'--output',str(self.root/'out'),
            '--env-file',str(self.root/'missing.env')],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.root/'out').exists())
        self.assertIn('"train": 2',result.stdout)

    def test_explicit_batch_budget_required(self):
        script=Path(__file__).resolve().parents[1]/'run_java5_batch.py'
        result=subprocess.run([sys.executable,str(script),'--data-root',str(self.source),
            '--source-manifest',str(self.manifest_path),'--output',str(self.root/'out'),'--execute'],
            capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('explicit',result.stderr.lower())
        self.assertFalse((self.root/'out').exists())

    def test_execute_and_resume_reuses_completed_model_steps(self):
        import run_java5_batch as module
        calls=[]
        def transport(url,key,payload,timeout):
            calls.append(payload['model'])
            extract='Extract concept performance' in payload['messages'][-1]['content']
            result={'observations':[{'concept_id':'java.input','status':'Unknown','description':'Insufficient evidence'}]} if extract else {'evaluation':'Model inference only'}
            return {'choices':[{'message':{'content':json.dumps(result)},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
        settings={'ZZZ_BASE_URL':'https://example.invalid/v1','ZZZ_API_KEY':'test-key',
                  'ZZZ_WEAK_MODEL':'luna-test','ZZZ_STRONG_MODEL':'terra-test'}
        output=self.root/'batch'
        with patch.object(module,'send_chat',transport):
            result=module.execute_batch(output,self.records,{'concepts':{'java.input':'Input'}},settings,4,8,self.root/'cache')
        self.assertEqual(result['completed_students'],['java_a','java_b'])
        self.assertEqual(result['splits'],{'test':2,'train':2})
        self.assertIn('terra-test',calls);self.assertIn('luna-test',calls)
        with patch.object(module,'send_chat',side_effect=AssertionError('Unexpected paid call on resume')):
            resumed=module.execute_batch(output,self.records,{'concepts':{'java.input':'Input'}},settings,4,8,self.root/'cache',resume=True)
        self.assertEqual(resumed['actual_requests'],result['actual_requests'])
        with patch.object(module,'send_chat',side_effect=AssertionError('Unexpected call')):
            with self.assertRaisesRegex(ValueError,'mismatch'):
                module.execute_batch(output,self.records,{'concepts':{'java.input':'Input'}},settings,5,10,self.root/'cache',resume=True)

    def test_partial_student_stops_batch_and_is_not_registered(self):
        import run_java5_batch as module
        settings={'ZZZ_BASE_URL':'https://example.invalid/v1','ZZZ_API_KEY':'test-key','ZZZ_MODEL':'test'}
        output=self.root/'partial'
        with patch.object(module,'send_chat',side_effect=ValueError('Rejected transport')):
            with self.assertRaisesRegex(RuntimeError,'incomplete'):
                module.execute_batch(output,self.records,{'concepts':{'java.input':'Input'}},settings,4,8,self.root/'cache')
        self.assertFalse((output/'java_b').exists())
        with self.api().DatasetStore(output/'dataset.sqlite3',self.records) as db:
            self.assertEqual(db.snapshot()['completed_students'],[])


if __name__=='__main__':unittest.main()
