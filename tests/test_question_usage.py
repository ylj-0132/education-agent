import json
import sys
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from profiling.run_store import RunStore, atomic_json, fingerprint
from profiling.java_execution import prepare_student_review
from generation.local_java import verify_local
from recommendation.history import load_usage_history
from recommendation.context import prepare_context, build_generation_request
from submissions.workflow import load_submission_evidence
import test_submissions
from test_recommendation import catalog


class UsageTests(unittest.TestCase):
    setUp=test_submissions.SubmissionTests.setUp
    client=test_submissions.SubmissionTests.client
    save=test_submissions.SubmissionTests.save

    def manifest(self,submissions=(),verifications=()):
        p=self.root/'history.json'
        atomic_json(p,{'schema':'question-usage-sources-v1','student_id':'s1',
            'generation_runs':['generation'],'submission_runs':list(submissions),
            'verification_runs':list(verifications)})
        return p

    def test_generated_unanswered_then_received_and_verified_in_handoff(self):
        manifest=self.manifest()
        history=load_usage_history(manifest,'s1')
        self.assertEqual(history[0]['status'],'generated_no_received_answer')
        self.assertFalse(history[0]['ready_for_student'])
        self.assertNotIn('reference_code',json.dumps(history))
        self.save();row=load_submission_evidence(self.root/'submission')
        cases=[{'input':'1','expected_output':'2','basis':'independent fixture oracle'}]
        package=prepare_student_review(row,cases,{'java':sys.executable,'javac':sys.executable})
        with RunStore(self.root/'verification',{'test':1},0) as store:
            verify_local(store,package,package['approval_sha256'],runner=lambda *a,**kw:
                {'status':'completed','returncode':0,'stdout':'2','stderr':''})
        history=load_usage_history(self.manifest(['submission'],['verification']),'s1')
        self.assertEqual(history[0]['status'],'answer_received')
        attempt=history[0]['attempts'][0]
        self.assertEqual(attempt['execution_observation']['outcome'],'passed_selected_tests')
        self.assertNotIn('STUDENT_ANSWER_SENTINEL',json.dumps(history))
        ctx=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'],usage_history=history)
        req=build_generation_request(ctx,selected_concept_ids=['a'],reason='practice')
        self.assertEqual(req['question_usage'],ctx['question_usage'])
        self.assertIn(row['problem_id'],req['previous_submission_problem_ids'])
        self.assertEqual(ctx['evidence'],[])  # usage is not mastery evidence

    def test_wrong_student_fixture_and_tampering_rejected(self):
        with self.assertRaises(ValueError):load_usage_history(self.manifest(),'other')
        self.save(dict(self.answer,origin='test_fixture'))
        with self.assertRaises(ValueError):load_usage_history(self.manifest(['submission']),'s1')
        p=self.root/'generation/generation.json';data=json.loads(p.read_text('utf-8'))
        data['versions'][-1]['question']['statement']='tampered';atomic_json(p,data)
        with self.assertRaises(ValueError):load_usage_history(self.manifest(),'s1')

    def test_duplicates_are_idempotent_and_filtered_by_scope(self):
        self.save();p=self.manifest(['submission','submission'])
        data=json.loads(p.read_text('utf-8'));data['generation_runs']*=2;atomic_json(p,data)
        history=load_usage_history(p,'s1')
        self.assertEqual(len(history),1);self.assertEqual(len(history[0]['attempts']),1)
        ctx=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['c'],usage_history=history)
        self.assertEqual(ctx['question_usage'],[])

    def test_cli_history_reaches_B_then_generation_and_teacher_entry(self):
        import run_recommendation as rec_cli
        import generate_question as gen_cli
        from generation.workflow import load_recommendation
        from test_generation import question, review
        from test_recommendation_comparison import response
        manifest=self.manifest();atomic_json(self.root/'catalog.json',catalog())
        env=self.root/'fake.env';env.write_text('ZZZ_API_KEY=fake\nZZZ_BASE_URL=https://example.com/v1\n'
            'ZZZ_STRONG_MODEL=gpt-5.6-terra\nZZZ_WEAK_MODEL=gpt-5.6-luna\n',encoding='utf-8')
        def rec_transport(url,key,payload,timeout):
            self.assertEqual(payload['model'],'gpt-5.6-luna')
            # The rules also mention this status; assert the actual context data,
            # not a keyword that can pass when all history records are omitted.
            content=payload['messages'][0]['content']
            sent=json.loads(content.split('CONTEXT_JSON:\n',1)[1].split('\nEND_CONTEXT',1)[0])
            self.assertEqual(sent.get('question_usage'),load_usage_history(manifest,'s1'))
            return response({'decision':'recommend','selected_candidate_id':None,'concept_ids':['a'],
                'reason':'诊断练习','evidence_ids':[],'relation_ids':[],'uncertainties':['尚无作答']})
        args=['run_recommendation.py','--catalog',str(self.root/'catalog.json'),'--student','s1','--scope','a',
              '--usage-history',str(manifest),'--env-file',str(env),'--output',str(self.root/'rec'),
              '--cache',str(self.root/'rec-cache'),'--execute']
        with patch.object(sys,'argv',args),patch.object(rec_cli,'send_chat',side_effect=rec_transport):rec_cli.main()
        req=load_recommendation(self.root/'rec');self.assertEqual(len(req['question_usage']),1)
        def gen_transport(url,key,payload,timeout):
            data=json.loads(payload['messages'][-1]['content'])
            self.assertEqual(payload['model'],'gpt-5.6-terra' if data['candidate'] else 'gpt-5.6-luna')
            self.assertEqual(len(data['request']['question_usage']),1)
            return response(review(data['candidate']) if data['candidate'] else question())
        for mode in ('recommendation','teacher'):
            origin=(['--recommendation-run',str(self.root/'rec')] if mode=='recommendation' else
                    ['--catalog',str(self.root/'catalog.json'),'--student','s1','--scope','a','--targets','a',
                     '--requirement','诊断','--usage-history',str(manifest)])
            argv=['generate_question.py',*origin,'--env-file',str(env),'--output',str(self.root/mode),
                  '--cache',str(self.root/(mode+'-cache')),'--execute']
            with patch.object(sys,'argv',argv),patch.object(gen_cli,'send_chat',side_effect=gen_transport):gen_cli.main()
            with patch.object(sys,'argv',argv+['--resume']),patch.object(gen_cli,'send_chat',side_effect=AssertionError('network')):gen_cli.main()

    def test_usage_data_survives_all_recommendation_prompt_layouts(self):
        from recommendation.comparison import make_model_input
        history=load_usage_history(self.manifest(),'s1')
        ctx=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'],usage_history=history)
        for task in ('direct','candidates','select'):
            for layout in ('single_user','split_roles'):
                with self.subTest(task=task,layout=layout):
                    prompt=make_model_input(ctx,task,message_layout=layout,candidates=[])
                    text=prompt if isinstance(prompt,str) else '\n'.join(m['content'] for m in prompt)
                    sent=json.loads(text.split('CONTEXT_JSON:\n',1)[1].split('\nEND_CONTEXT',1)[0])
                    self.assertEqual(sent.get('question_usage'),history)
        empty=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['c'],usage_history=history)
        omitted=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'])
        for ctx, supplied in ((empty,True),(omitted,False)):
            text=make_model_input(ctx,'direct')
            sent=json.loads(text.split('CONTEXT_JSON:\n',1)[1].split('\nEND_CONTEXT',1)[0])
            self.assertEqual('question_usage' in sent,supplied)
            if supplied:self.assertEqual(sent['question_usage'],[])

    def test_received_usage_is_not_automatically_profiled_and_conflicts_rejected(self):
        from test_recommendation import profile
        self.save();row=load_submission_evidence(self.root/'submission')
        history=load_usage_history(self.manifest(['submission']),'s1')
        p=profile()
        ctx=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'],profile=p,usage_history=history)
        self.assertEqual(ctx['question_usage'][0]['attempts'][0]['profile_evidence_status'],'not_in_supplied_profile')
        p['evidence'].append(row)
        ctx=prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'],profile=p,usage_history=history)
        self.assertEqual(ctx['question_usage'][0]['attempts'][0]['profile_evidence_status'],'included')
        wrong=deepcopy(history);wrong[0]['attempts'][0]['program_sha256']='wrong'
        with self.assertRaises(ValueError):prepare_context(catalog(),student_id='s1',allowed_concept_ids=['a'],profile=p,usage_history=wrong)

    def test_foreign_verification_and_changed_receipt_fail_closed(self):
        from test_recommendation import profile
        self.save()
        package=prepare_student_review(profile()['evidence'][0],
            [{'input':'','expected_output':'','basis':'fake fixture'}],{'java':sys.executable,'javac':sys.executable})
        with RunStore(self.root/'verification',{'test':1},0) as store:
            verify_local(store,package,package['approval_sha256'],runner=lambda *a,**kw:
                {'status':'completed','returncode':0,'stdout':'','stderr':''})
        with self.assertRaises(ValueError):load_usage_history(self.manifest(['submission'],['verification']),'s1')
        path=self.root/'submission/submission.json';data=json.loads(path.read_text('utf-8'))
        data['evidence']['program']='changed';atomic_json(path,data)
        with self.assertRaises(ValueError):load_usage_history(self.manifest(['submission']),'s1')
