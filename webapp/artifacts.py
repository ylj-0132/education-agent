"""Local result inventory, allowlisted reads and answer draft storage."""
import json
import re
import uuid
from pathlib import Path

KINDS = {
    'profile.json': 'profiles',
    'generation.json': 'generations',
    'recommendation.json': 'recommendations',
    'verification.json': 'verification_runs',
    'submission.json': 'submission_runs',
}


class ArtifactStore:
    """Read saved results without starting tasks or changing learning evidence."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def safe_path(self, value):
        """Resolve a public repository-relative path, including symlink containment."""
        if (
            not isinstance(value, str) or not value or '\\' in value or ':' in value
            or Path(value).is_absolute() or '..' in Path(value).parts
        ):
            raise ValueError('Only repository relative paths are accepted')
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root) or any(p.startswith('.') for p in Path(value).parts):
            raise ValueError('Path is outside allowed repository data')
        return path

    def inventory(self):
        """Index recognized results and their student links, without changing files."""
        groups = {key: [] for key in (
            'profiles', 'generations', 'recommendations', 'record_files',
            'usage_files', 'submission_runs', 'verification_runs',
        )}
        allowed = {}
        loaded = {}
        candidates = [self.root / 'config/java-basics.json']
        for folder in ('runs', 'artifacts'):
            base = self.root / folder
            if base.exists():
                candidates.extend(base.rglob('*.json'))
        for path in sorted(candidates):
            rel = path.relative_to(self.root).as_posix()
            if path.name in ('trace.json', 'request.json') or not path.resolve().is_relative_to(self.root):
                continue
            kind = KINDS.get(path.name)
            if path.parent.name == 'usage-history':
                kind = 'usage_files'
            if path.name in ('records.json', 'new-evidence.json', 'review-batch.json', 'train-evidence.json'):
                kind = 'record_files'
            if not kind and rel != 'config/java-basics.json':
                continue
            try:
                if path.stat().st_size > 8_000_000:
                    continue
                data = json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get('schema') == 'question-usage-sources-v1':
                kind = 'usage_files'
            elif kind == 'usage_files':
                continue
            if kind == 'record_files' and (
                not isinstance(data, list) or not data
                or any(
                    not isinstance(row, dict) or not row.get('question') or not row.get('program')
                    for row in data
                )
            ):
                continue
            allowed[rel] = path
            loaded[rel] = data
            if kind:
                info = data if isinstance(data, dict) else {}
                groups[kind].append(dict(
                    path=rel,
                    student_id=info.get('student_id'),
                    status=info.get('status', info.get('execution_status', 'available')),
                    label=path.parent.name,
                ))

        # Reference verification has no student answer; link it through the generated question.
        generation_students = {}
        for item in groups['generations']:
            info = loaded[item['path']]
            if info.get('question_id') and item['student_id']:
                generation_students.setdefault(info['question_id'], set()).add(item['student_id'])
        # A receipt is usable only when its recorded identities agree and origin is student.
        for item in groups['submission_runs']:
            info = loaded[item['path']]
            packet = info.get('packet', {})
            evidence = info.get('evidence', {})
            answer = packet.get('answer', {})
            identities = {
                value for value in (
                    evidence.get('student_id'), answer.get('student_id'),
                    packet.get('assignment', {}).get('student_id'),
                )
                if isinstance(value, str) and value
            }
            item['student_id'] = next(iter(identities)) if len(identities) == 1 else None
            item['learning_evidence_eligible'] = (
                info.get('learning_evidence_eligible') is True
                and answer.get('origin') == 'student' and bool(item['student_id'])
            )
        for item in groups['verification_runs']:
            path = allowed[item['path']].parent / 'approval.json'
            approval = {}
            try:
                if path.resolve().is_relative_to(self.root) and path.stat().st_size <= 8_000_000:
                    approval = json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                pass
            question = approval.get('question', {}) if isinstance(approval, dict) else {}
            evidence = question.get('student_evidence') if isinstance(question, dict) else None
            if isinstance(evidence, dict):
                item['verification_kind'] = 'student'
                item['student_id'] = evidence.get('student_id')
            else:
                item['verification_kind'] = 'reference'
                students = generation_students.get(loaded[item['path']].get('question_id'), set())
                item['student_id'] = next(iter(students)) if len(students) == 1 else None
        for item in groups['record_files']:
            students = sorted({
                row['student_id'] for row in loaded[item['path']]
                if isinstance(row.get('student_id'), str) and row['student_id']
            })
            item['student_ids'] = students
            item['student_id'] = students[0] if len(students) == 1 else None
        for items in groups.values():
            for item in items:
                # This means a file-level student link, not authenticated identity.
                item['identity_verified'] = bool(item['student_id'])
        return groups, allowed

    def artifact(self, value):
        self.safe_path(value)
        _, allowed = self.inventory()
        if value not in allowed:
            raise ValueError('Artifact is not in the public result allowlist')
        return json.loads(allowed[value].read_text(encoding='utf-8-sig'))

    def save_submission_draft(self, answer):
        """Save an input draft; submission acceptance and profile updates are separate."""
        if (
            not isinstance(answer, dict) or answer.get('schema') != 'student-answer-v1'
            or answer.get('origin') not in ('student', 'test_fixture')
        ):
            raise ValueError('Expected student-answer-v1 with explicit student/test_fixture origin')
        for field in ('submission_id', 'student_id', 'question_id', 'question_sha256', 'program'):
            if not isinstance(answer.get(field), str) or not answer[field]:
                raise ValueError('Missing answer field: ' + field)
        if not re.fullmatch('[0-9a-fA-F]{64}', answer['question_sha256']):
            raise ValueError('Invalid question SHA256')
        content = json.dumps(answer, ensure_ascii=False, indent=2)
        if len(content.encode('utf-8')) > 60000:
            raise ValueError('Answer draft too large')
        folder = self.safe_path('artifacts/workbench-inputs')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (uuid.uuid4().hex + '.json')
        with path.open('x', encoding='utf-8') as handle:
            handle.write(content)
        return path.relative_to(self.root).as_posix()
