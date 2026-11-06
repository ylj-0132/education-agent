import json
import tempfile
import unittest
from pathlib import Path

from java5_data import load_records, review_batch, export_data


def row(pid, error='No error.'):
    return dict(problem_id=pid, question='原始题干 &lt; 3', desc='外部描述',
                program='public class Main {}', error_desc=error)


class DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'input'
        for sid in ['java_a', 'java_b']:
            folder=self.root/sid
            folder.mkdir(parents=True)
            (folder/'train.json').write_text(json.dumps([row('p1'), row('p2','Bug'), row('p3'), row('p4')]), encoding='utf-8')
            (folder/'test.json').write_text(json.dumps([row('p5','SECRET_TEST')]), encoding='utf-8')

    def test_provenance_and_unverified_analysis(self):
        r=load_records(self.root)[0]
        self.assertEqual(r['source']['file'], 'java_a/train.json')
        self.assertEqual(r['source']['index'], 0)
        self.assertEqual(len(r['source']['sha256']),64)
        self.assertEqual(r['question'], '原始题干 &lt; 3')
        self.assertEqual(r['annotations']['verification'], 'unverified')
        self.assertNotIn('correct',r)

    def test_review_is_training_only_and_covers_students(self):
        batch=review_batch(load_records(self.root),3)
        self.assertEqual(len(batch),6)
        self.assertEqual({r['student_id'] for r in batch},{'java_a','java_b'})
        self.assertTrue(all(r['split']=='train' for r in batch))
        self.assertEqual(sum(r['annotations']['error_desc']=='Bug' for r in batch),2)

    def test_reject_missing_field(self):
        p=self.root/'java_a/train.json'
        p.write_text('[{"problem_id":"p1"}]',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'question'):load_records(self.root)

    def test_reject_within_student_overlap(self):
        (self.root/'java_a/test.json').write_text(json.dumps([row('p1')]),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'overlap'):load_records(self.root)

    def test_reject_inconsistent_question(self):
        changed=row('p1');changed['question']='Different'
        (self.root/'java_b/train.json').write_text(json.dumps([changed]),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'question conflict'):load_records(self.root)

    def test_export_separates_test_and_does_not_overwrite(self):
        out=Path(self.tmp.name)/'out'
        export_data(self.root,out)
        train=(out/'train-evidence.json').read_text(encoding='utf-8')
        batch=(out/'review-batch.json').read_text(encoding='utf-8')
        self.assertNotIn('SECRET_TEST',train+batch)
        questions=json.loads((out/'train-questions.json').read_text(encoding='utf-8'))
        self.assertNotIn('program',str(questions))
        with self.assertRaises(FileExistsError):export_data(self.root,out)

    def test_output_cannot_be_in_input(self):
        with self.assertRaises(ValueError):export_data(self.root,self.root/'output')

    def test_missing_split_is_not_silently_skipped(self):
        (self.root/'java_a/test.json').unlink()
        with self.assertRaises(FileNotFoundError):load_records(self.root)

    def test_empty_source_rejected(self):
        empty=Path(self.tmp.name)/'empty';empty.mkdir()
        with self.assertRaises(ValueError):load_records(empty)


if __name__=='__main__':unittest.main()
