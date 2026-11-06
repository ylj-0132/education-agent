import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
from profiling.api_client import CachedChatClient, model_config
from profiling.student_profile import StudentProfile
from profiling.run_store import RunStore, RecordedTransport
from profiling.reliable_runner import run_profile
from test_recommendation import profile, catalog
from test_recommendation_comparison import response


class RoutingTests(unittest.TestCase):
    def test_generation_keeps_luna_and_routes_review_to_terra(self):
        from generation.workflow import run_generation
        from test_generation import request, question, review
        settings={'ZZZ_STRONG_MODEL':'gpt-5.6-terra','ZZZ_WEAK_MODEL':'gpt-5.6-luna'}
        cfg=model_config(settings,generation=True)
        q=question(); bad=review(q);bad['checks'][0]['status']='fail'
        q2=dict(q,title='修订版数组求和')
        values=iter([q,bad,q2,review(q2)]);seen=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with RunStore(root/'run',cfg,8) as store:
                def transport(url,key,payload,timeout):
                    seen.append(payload['model']);return response(next(values))
                client=CachedChatClient(base_url='https://example.com/v1',api_key='fake',cache_dir=root/'cache',
                    max_calls=8,transport=RecordedTransport(store,transport),event_sink=store.api_event,**cfg)
                result=run_generation(store,client,request())
            self.assertEqual(client.model_for('generate_question'),'gpt-5.6-luna')
            self.assertEqual(client.model_for('review_question'),'gpt-5.6-terra')
            self.assertEqual(seen,['gpt-5.6-luna','gpt-5.6-terra','gpt-5.6-luna','gpt-5.6-terra'])
            self.assertEqual(result['status'],'needs_review')
        self.assertEqual(model_config({'ZZZ_MODEL':'legacy'},generation=True),
                         {'model':'legacy','stage_models':{'review_question':'legacy'}})

    def test_stage_routing_provenance_budget_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);seen=[]
            settings={'ZZZ_STRONG_MODEL':'gpt-5.6-terra','ZZZ_WEAK_MODEL':'gpt-5.6-luna'}
            config=model_config(settings, profiling=True)
            def transport(url,key,payload,timeout):
                seen.append(payload)
                return response({'observations':[{'concept_id':'a','status':'Good','description':'本题表现'}]}
                    if payload['model']=='gpt-5.6-terra' else {'evaluation':'仅限本题'})
            def client(store,fn):
                return CachedChatClient(base_url='https://example.com/v1',api_key='fake',cache_dir=root/'cache',
                    max_calls=2,transport=RecordedTransport(store,fn),event_sink=store.api_event,**config)
            with RunStore(root/'run',config,2) as store:
                c=client(store,transport)
                result=run_profile(store,c,profile()['evidence'][:1],catalog()['concepts'],c.model)
                with self.assertRaises(RuntimeError):c.complete('summarize','another')
            self.assertEqual([p['model'] for p in seen],['gpt-5.6-terra','gpt-5.6-luna'])
            self.assertTrue(all(p.get('reasoning_effort')=='low' and 'max_tokens' not in p for p in seen))
            self.assertEqual(result['observations'][0]['model_id'],'gpt-5.6-terra')
            self.assertEqual(result['summaries']['a']['model_id'],'gpt-5.6-luna')
            with RunStore(root/'run',config,2,resume=True) as store:
                c=client(store,lambda *a:self.fail('network'))
                self.assertEqual(run_profile(store,c,profile()['evidence'][:1],catalog()['concepts'],c.model),result)
            changed=model_config(dict(settings,ZZZ_STRONG_MODEL='other'),profiling=True)
            with self.assertRaises(ValueError):RunStore(root/'run',changed,2,resume=True)

    def test_legacy_fallback_and_weak_tasks(self):
        self.assertEqual(model_config({'ZZZ_MODEL':'old'},profiling=True),
                         {'model':'old','stage_models':{'extract':'old'}})
        cfg=model_config({'ZZZ_MODEL':'old','ZZZ_WEAK_MODEL':'weak','ZZZ_STRONG_MODEL':'strong'})
        self.assertEqual(cfg,{'model':'weak','stage_models':{}})

    def test_profile_cli_uses_two_models_and_binds_resume_configuration(self):
        import run_profile as cli
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,value in [('rows',profile()['evidence'][:1]),('catalog',catalog())]:
                (root/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
            env=root/'fake.env'
            env.write_text('ZZZ_API_KEY=fake\nZZZ_BASE_URL=https://example.com/v1\n'
                'ZZZ_STRONG_MODEL=gpt-5.6-terra\nZZZ_WEAK_MODEL=gpt-5.6-luna\n',encoding='utf-8')
            seen=[]
            def transport(url,key,payload,timeout):
                seen.append(payload['model'])
                return response({'observations':[{'concept_id':'a','status':'Good','description':'仅测试'}]}
                    if payload['model']=='gpt-5.6-terra' else {'evaluation':'测试汇总'})
            argv=['run_profile.py','--records',str(root/'rows.json'),'--concepts',str(root/'catalog.json'),
                  '--student','s1','--env-file',str(env),'--output',str(root/'out'),
                  '--cache',str(root/'cache'),'--execute']
            with patch.object(sys,'argv',argv),patch.object(cli,'send_chat',side_effect=transport):cli.main()
            self.assertEqual(seen,['gpt-5.6-terra','gpt-5.6-luna'])
            with patch.object(sys,'argv',argv+['--resume']),patch.object(cli,'send_chat',side_effect=AssertionError('network')):cli.main()
            env.write_text(env.read_text().replace('gpt-5.6-terra','other'),encoding='utf-8')
            with patch.object(sys,'argv',argv+['--resume']),patch.object(cli,'send_chat',side_effect=AssertionError('network')):
                with self.assertRaisesRegex(ValueError,'mismatch'):cli.main()
