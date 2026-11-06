"""Shared eligibility checks for historical and received student evidence."""
from datetime import datetime
from .run_store import fingerprint


def validate_timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must include a timezone')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Invalid timestamp') from None
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')


def validate_learning_evidence(row, student_id):
    if not isinstance(row, dict) or row.get('student_id') != student_id:
        raise ValueError('Evidence belongs to another student')
    origin = row.get('evidence_origin')
    if row.get('split') == 'train':
        if origin not in (None, 'historical_student_submission') or 'submission' in row:
            raise ValueError('Synthetic or live evidence cannot be relabelled as training data')
        return
    if row.get('split') != 'live' or origin != 'student_submission':
        raise ValueError('Only training or declared real student submissions are learning evidence')
    meta = row.get('submission')
    required = ('submission_id','question_id','question_sha256','generation_run_id','received_at','program_sha256','question_text_sha256')
    if not isinstance(meta, dict) or any(not isinstance(meta.get(k),str) or not meta[k].strip() for k in required):
        raise ValueError('Live evidence requires a bound submission receipt')
    if type(meta.get('question_version')) is not int or meta['question_version'] < 1:
        raise ValueError('Invalid question version')
    validate_timestamp(meta['received_at'])
    if meta.get('submitted_at') is not None:
        validate_timestamp(meta['submitted_at'])
    expected_id = 'submission:' + fingerprint([student_id, meta['submission_id']])
    if (row.get('record_id') != expected_id or row.get('problem_id') != meta['question_id']
            or fingerprint(row.get('program')) != meta['program_sha256']
            or fingerprint(row.get('question')) != meta['question_text_sha256']):
        raise ValueError('Submission evidence binding changed')
