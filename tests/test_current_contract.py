import json
from pathlib import Path
import subprocess
import sys
import unittest

from recommendation.context import prepare_context
from test_recommendation import catalog


class CurrentContractTests(unittest.TestCase):
    def test_profile_resume_requires_explicit_execution(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-B', 'run_profile.py', '--resume'],
                                cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--resume requires --execute', result.stderr)

    def test_context_does_not_reopen_settled_recommendation_policy(self):
        context = prepare_context(catalog(), student_id='s1', allowed_concept_ids=['a'])
        self.assertEqual(context['selection_policy'], 'model_direct')
        self.assertNotIn('pending_decisions', context)

    def test_generation_preview_explains_separate_java_approval(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-B', 'generate_question.py',
            '--student', 'preview-only', '--scope', 'java.array', '--targets', 'java.array',
            '--requirement', 'diagnostic'], cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['java_execution'], 'separate_explicit_approval')
