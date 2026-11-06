import copy
import tempfile
import unittest
from pathlib import Path
from profiling.run_store import RunStore, fingerprint


class ContinuationTests(unittest.TestCase):
    def test_inherit_attempts_successes_and_no_third_attempt(self):
        from continue_java5_three import inherit
        request={'url':'https://example.invalid','payload':{'model':'fake'}}
        a={'id':'a','step_id':'blocked','request_key':fingerprint(request),'request':request,
           'number':1,'status':'failed','retryable':False}
        source={'run_id':'old','attempts':[a], 'steps':{'done':{'status':'succeeded','result':[1]}}}
        diagnostic={'run_id':'retry','attempts':[dict(a,id='b')]}
        original=copy.deepcopy(source)
        with tempfile.TemporaryDirectory() as temp:
            with RunStore(Path(temp)/'new',{},3) as store:
                inherit(store,source,diagnostic)
                self.assertEqual(store.start_step('done',{}),[1])
                store.start_step('blocked',{})
                with self.assertRaises(RuntimeError):store.reserve(request['url'],request['payload'])
                store.start_step('next',{})
                store.reserve(request['url'],request['payload'])
                self.assertEqual(len(store.state['attempts']),3)
                store.start_step('over-budget',{})
                with self.assertRaisesRegex(RuntimeError,'limit'):store.reserve(request['url'],request['payload'])
        self.assertEqual(source,original)

    def test_unknown_attempt_not_migrated(self):
        from continue_java5_three import inherit
        with tempfile.TemporaryDirectory() as temp:
            with RunStore(Path(temp)/'new',{},3) as store:
                with self.assertRaisesRegex(ValueError,'unknown'):
                    inherit(store,{'run_id':'old','attempts':[{'status':'sent'}],'steps':{}},None)

if __name__=='__main__':unittest.main()
