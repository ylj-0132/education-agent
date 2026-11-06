import unittest
from profiling.student_profile import StudentProfile


def record(student='a', split='train'):
    return {'record_id':'r1','student_id':student,'split':split,'problem_id':'p1',
            'question':'Q','program':'CODE','annotations':{'desc':'D','error_desc':'E'},
            'source':{'file':'a/train.json','index':0,'sha256':'hash'}}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def complete(stage,prompt):
            self.calls.append((stage,prompt))
            if stage=='extract':
                return {'observations':[{'concept_id':'loops','status':'Bad','description':'loop condition'}]}
            return {'evaluation':'Needs review'}
        self.profile=StudentProfile('a',{'loops':'循环','arrays':'数组'},complete,model_id='fake-test')

    def test_evidence_to_summary_and_retrieval(self):
        result=self.profile.build([record()])
        self.assertEqual(result['observations'][0]['evidence_id'],'r1')
        self.assertEqual(result['summaries']['loops']['evidence_ids'],['r1'])
        self.assertEqual(result['summaries']['loops']['kind'],'model_inference')
        context=self.profile.retrieve(result,['loops'])
        self.assertEqual(context['evidence'][0]['program'],'CODE')
        self.assertEqual(self.profile.retrieve(result,['arrays'])['evidence'],[])
        self.assertEqual([s for s,p in self.calls],['extract','summarize'])

    def test_reject_mixed_student_or_test_before_call(self):
        for bad in [record('b'),record(split='test')]:
            with self.assertRaises(ValueError):self.profile.build([record(),bad])
        self.assertEqual(self.calls,[])

    def test_bad_model_output(self):
        for obs in [dict(concept_id='invented',status='Good',description='x'),
                    dict(concept_id='loops',status='maybe',description='x')]:
            self.profile.complete=lambda *a: {'observations':[obs]}
            with self.assertRaises(ValueError):self.profile.build([record()])

    def test_retrieve_other_student_rejected(self):
        result=self.profile.build([record()]);result['student_id']='b'
        with self.assertRaises(ValueError):self.profile.retrieve(result,['loops'])

    def test_duplicate_evidence_rejected_before_call(self):
        with self.assertRaises(ValueError):self.profile.build([record(),record()])
        self.assertEqual(self.calls,[])

    def test_no_evidence_does_not_call_model(self):
        self.assertEqual(self.profile.build([])['summaries'],{})
        self.assertEqual(self.calls,[])

    def test_external_annotations_never_enter_model_inputs(self):
        r=record()
        r['annotations']={'desc':'UNTRUSTED_DESCRIPTION_SENTINEL',
                          'error_desc':'UNTRUSTED_ERROR_SENTINEL'}
        result=self.profile.build([r])
        for _,prompt in self.calls:
            for value in r['annotations'].values():
                self.assertNotIn(value,prompt)
        self.assertIn('Question:\nQ',self.calls[0][1])
        self.assertIn('Student Program:\nCODE',self.calls[0][1])
        self.assertEqual(result['evidence'][0],r)

    def test_external_annotations_are_optional_and_do_not_change_prompt(self):
        baseline=record()
        self.profile.build([baseline])
        expected=self.calls[0][1]
        for annotations in [None,{}, {'desc':'different','error_desc':''}]:
            r=record()
            if annotations is None:
                del r['annotations']
            else:
                r['annotations']=annotations
            self.calls.clear()
            self.profile.build([r])
            self.assertEqual(self.calls[0][1],expected)
