from copy import deepcopy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

from profiling.java_execution import prepare_student_review, attach_verifications, execution_observation, execution_model_input
from profiling.run_store import RunStore, atomic_json
from generation.local_java import verify_local
from profiling.student_profile import StudentProfile
from recommendation.context import prepare_context
from test_recommendation import profile, catalog


class StudentExecutionTests(unittest.TestCase):
    def test_all_passed_model_input_has_summary_without_case_details(self):
        self.verified('pass');row=attach_verifications([self.row],[self.root/'verify'])[0]
        compact=execution_model_input(row)
        self.assertEqual(compact['summary'],'测试用例通过（2/2）。')
        self.assertNotIn('case_results',compact)
        self.assertNotIn('compile',compact)
        self.assertNotIn('expected_output',json.dumps(compact))

    def test_mixed_model_input_only_contains_failed_cases_and_unexecuted_count(self):
        self.cases.append({'input':'3 3\n','expected_output':'6\n','basis':'3+3=6'})
        p=prepare_student_review(self.row,self.cases,self.tools);calls=[]
        def runner(*args,**kwargs):
            calls.append(1)
            return {'status':'completed','returncode':0,'stdout':['','3\n','9\n'][len(calls)-1],'stderr':''}
        with RunStore(self.root/'verify',{'test':1},0) as store:verify_local(store,p,p['approval_sha256'],runner=runner)
        row=attach_verifications([self.row],[self.root/'verify'])[0];compact=execution_model_input(row)
        self.assertEqual((compact['passed_cases'],compact['planned_cases'],compact['executed_cases'],compact['unexecuted_cases']),(1,3,2,1))
        self.assertEqual([c['index'] for c in compact['case_results']],[1])
        self.assertEqual(compact['case_results'][0]['input'],'2 2\n')
        self.assertEqual(compact['case_results'][0]['expected_output'],'4\n')
        self.assertEqual(compact['case_results'][0]['actual_output'],'9\n')
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.row=deepcopy(profile()['evidence'][0])
        self.cases=[{'input':'1 2\n','expected_output':'3\n','basis':'1+2=3'},
                    {'input':'2 2\n','expected_output':'4\n','basis':'2+2=4'}]
        self.tools={'java':sys.executable,'javac':sys.executable}

    def verified(self,mode='wrong'):
        p=prepare_student_review(self.row,self.cases,self.tools);calls=[]
        def runner(command,**kwargs):
            calls.append(command)
            return {'status':'timeout' if mode=='timeout' and len(calls)>1 else 'completed',
                    'returncode':1 if mode=='compile' or (mode=='runtime' and len(calls)>1) else 0,
                    'stdout':'' if len(calls)==1 else ('9\n' if mode=='wrong' else str(sum(map(int,kwargs['stdin'].split())))+'\n'),
                    'stderr':'compile diagnostic' if mode=='compile' else ''}
        with RunStore(self.root/'verify',{'test':1},0) as store:
            result=verify_local(store,p,p['approval_sha256'],runner=runner)
        return p,result

    def test_bound_evidence_is_passed_to_model_without_external_annotations(self):
        self.verified();rows=attach_verifications([self.row],[self.root/'verify'])
        observed=execution_observation(rows[0]);self.assertEqual(observed['outcome'],'wrong_answer')
        self.assertEqual(observed['executed_cases'],1);self.assertEqual(observed['unexecuted_cases'],1)
        self.assertEqual(observed['case_results'][0]['actual_output'],'9\n')
        prompts=[]
        builder=StudentProfile('s1',catalog()['concepts'],lambda stage,prompt:prompts.append(prompt) or {'observations':[]},model_id='fake')
        builder.insert(rows)
        self.assertIn('Execution observations',prompts[0]);self.assertIn('wrong_answer',prompts[0])
        self.assertNotIn('EXTERNAL_',prompts[0]);self.assertNotIn('execution_verification',self.row)
        p=profile();p['evidence'][0]=rows[0]
        ctx=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'],profile=p)
        self.assertEqual(ctx['evidence'][0]['execution_observation']['outcome'],'wrong_answer')

    def test_wrong_student_question_code_and_tampered_result_rejected(self):
        self.verified()
        for change in [dict(student_id='other'),dict(problem_id='other'),dict(program='other'),dict(question='changed')]:
            with self.assertRaises(ValueError):attach_verifications([dict(self.row,**change)],[self.root/'verify'])
        p=self.root/'verify/verification.json';r=json.loads(p.read_text('utf-8'));r['cases'][0]['passed']=True;atomic_json(p,r)
        with self.assertRaises(ValueError):attach_verifications([self.row],[self.root/'verify'])

    def test_compile_failure_is_not_a_wrong_answer_or_mastery_score(self):
        self.verified('compile');row=attach_verifications([self.row],[self.root/'verify'])[0]
        o=execution_observation(row);self.assertEqual(o['outcome'],'compile_error')
        self.assertEqual(o['executed_cases'],0);self.assertEqual(o['unexecuted_cases'],2)
        self.assertNotIn('mastery',o)

    def test_passes_are_limited_and_success_resume_does_not_execute(self):
        p,r=self.verified('pass')
        with RunStore(self.root/'verify',{'test':1},0,resume=True) as store:
            resumed=verify_local(store,p,p['approval_sha256'],runner=lambda *a,**kw:self.fail('rerun'))
        self.assertEqual(r,resumed)
        row=attach_verifications([self.row],[self.root/'verify'])[0]
        self.assertEqual(execution_observation(row)['outcome'],'passed_selected_tests')
        with self.assertRaises(ValueError):attach_verifications([self.row],[self.root/'verify',self.root/'verify'])

    def test_unapproved_and_unknown_cannot_be_used_as_judge_evidence(self):
        p=prepare_student_review(self.row,self.cases,self.tools)
        with RunStore(self.root/'verify',{'test':1},0) as store:
            with self.assertRaises(ValueError):verify_local(store,p,'wrong',runner=lambda *a,**kw:self.fail('execution'))
            def interrupt(*a,**kw):raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):verify_local(store,p,p['approval_sha256'],runner=interrupt)
        with RunStore(self.root/'verify',{'test':1},0,resume=True) as store:
            r=verify_local(store,p,p['approval_sha256'],runner=lambda *a,**kw:self.fail('rerun'))
        self.assertEqual(r['status'],'execution_unknown')
        with self.assertRaises(ValueError):attach_verifications([self.row],[self.root/'verify'])

    def test_runtime_and_timeout_are_distinct(self):
        for mode,expected in [('runtime','runtime_error'),('timeout','timeout')]:
            with tempfile.TemporaryDirectory() as tmp:
                self.root=Path(tmp)
                self.verified(mode)
                row=attach_verifications([self.row],[self.root/'verify'])[0]
                self.assertEqual(execution_observation(row)['outcome'],expected)

    def test_cli_preview_never_runs_and_analysis_preview_needs_no_key(self):
        for name,data in [('rows',[self.row]),('cases',self.cases),('catalog',catalog())]:
            atomic_json(self.root/(name+'.json'),data)
        bin_dir=self.root/'jdk/bin';bin_dir.mkdir(parents=True)
        for name in ('java.exe','javac.exe'):(bin_dir/name).write_bytes(b'not an executable')
        args=[sys.executable,'verify_submission.py','--records',str(self.root/'rows.json'),
            '--cases',str(self.root/'cases.json'),'--java-home',str(self.root/'jdk'),
            '--output',str(self.root/'never')]
        r=subprocess.run(args,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse((self.root/'never').exists())
        r=subprocess.run(args+['--execute'],capture_output=True,text=True);self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'never').exists())
        self.verified()
        r=subprocess.run([sys.executable,'run_profile.py','--records',str(self.root/'rows.json'),
            '--concepts',str(self.root/'catalog.json'),'--student','s1','--verification-run',str(self.root/'verify'),
            '--env-file',str(self.root/'missing.env')],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
