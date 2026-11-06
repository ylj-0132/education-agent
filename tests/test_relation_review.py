from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest

from recommendation.comparison import validate_decision, review_relations
from recommendation.runner import run_recommendation
from generation.workflow import load_recommendation
from profiling.api_client import CachedChatClient
from profiling.run_store import RunStore, RecordedTransport
from test_recommendation_comparison import context, decision, response


class RelationReviewTests(unittest.TestCase):
    def test_partial_association_is_auxiliary_not_a_new_target(self):
        ctx=context(); d=dict(decision(), relation_ids=['r1']); before=deepcopy(d)
        validate_decision(ctx,d)
        r=review_relations(ctx,d)
        self.assertEqual(r['accepted_relation_ids'],[])
        self.assertEqual(r['auxiliary_relation_ids'],['r1'])
        self.assertEqual(r['issues'][0]['missing_concept_ids'],['b'])
        self.assertEqual(d,before)

    def test_unknown_and_unrelated_are_excluded_not_fatal(self):
        ctx=context(); ctx['relations']['associations'].append({'id':'other','concept_ids':['b','c']})
        d=dict(decision(),relation_ids=['missing','other'])
        validate_decision(ctx,d)
        r=review_relations(ctx,d)
        self.assertEqual(r['excluded_relation_ids'],['missing','other'])
        self.assertEqual(r['accepted_relation_ids'],[])

    def test_supported_relations_and_hard_checks(self):
        ctx=context()
        d=dict(decision(),concept_ids=['a','b'],relation_ids=['r1'])
        self.assertEqual(review_relations(ctx,d)['accepted_relation_ids'],['r1'])
        for changes in [dict(concept_ids=['outside']),dict(evidence_ids=['foreign']),dict(relation_ids='r1'),dict(relation_ids=['r1','r1'])]:
            with self.assertRaises(ValueError):validate_decision(ctx,dict(d,**changes))

    def test_runner_no_retry_and_handoff_preserves_warning_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ctx=context();d=dict(decision(),relation_ids=['r1','missing'])
            def client(store,transport):
                return CachedChatClient(base_url='https://example.com/v1',api_key='fake',model='test',cache_dir=root/'cache',max_calls=2,transport=RecordedTransport(store,transport),event_sink=store.api_event)
            with RunStore(root/'run',{'test':1},2) as store:
                r=run_recommendation(store,client(store,lambda *a:response(d)),ctx)
                self.assertEqual(len(store.state['attempts']),1)
            self.assertEqual(r['status'],'recommended')
            self.assertEqual(r['decision'],d)
            req=load_recommendation(root/'run')
            self.assertEqual(req['recommendation_relation_ids'],[])
            self.assertEqual(req['recommendation_relation_review']['auxiliary_relation_ids'],['r1'])
            self.assertEqual(req['recommendation_relation_review']['excluded_relation_ids'],['missing'])
            with RunStore(root/'run',{'test':1},2,resume=True) as store:
                resumed=run_recommendation(store,client(store,lambda *a:self.fail('network')),ctx)
            self.assertEqual(resumed['generation_request'],req)
            p=root/'run/recommendation.json';bad=json.loads(p.read_text('utf-8'))
            bad['generation_request']['recommendation_relation_review']['auxiliary_relation_ids']=[]
            p.write_text(json.dumps(bad),encoding='utf-8')
            with self.assertRaises(ValueError):load_recommendation(root/'run')
