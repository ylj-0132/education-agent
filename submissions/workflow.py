"""Bind submissions to immutable generated questions before profile analysis."""
from copy import deepcopy
import json
from pathlib import Path

from profiling.run_store import RunStore, atomic_json, fingerprint, stamp
from profiling.evidence import validate_learning_evidence, validate_timestamp
from generation.workflow import validate_request, validate_question
from verify_java import load_question

SCHEMA = 'submission-receipt-v1'


def load_assignment(path):
    path = Path(path)
    q, qid = load_question(path)
    state = RunStore.inspect(path, full=True)
    report = json.loads((path/'generation.json').read_text(encoding='utf-8'))
    req = json.loads((path/'request.json').read_text(encoding='utf-8'))
    validate_request(req)
    validate_question(req, q)
    if (fingerprint(req) != state['generation_round']['request_sha256']
            or report.get('student_id') != req['student_id']
            or report.get('request_sha256') != fingerprint(req)):
        raise ValueError('Generation request/student binding changed')
    return {'question_id': qid, 'question_sha256': fingerprint(q),
            'question_version': report['versions'][-1]['version'], 'question': q,
            'student_id': req['student_id'], 'generation_run_id': state['run_id'],
            'recommendation_id': req.get('recommendation_id'),
            'generation_status': report['status'], 'ready_for_student': report['ready_for_student'],
            'generation_path': str(path.resolve())}


def prepare_submission(generation_path, answer):
    assignment = load_assignment(generation_path)
    fields = {'schema','submission_id','student_id','question_id','question_sha256','origin','program','submitted_at'}
    if not isinstance(answer, dict) or set(answer) != fields or answer['schema'] != 'student-answer-v1':
        raise ValueError('Expected student-answer-v1 fields')
    for key in ('submission_id','student_id','question_id','question_sha256','program'):
        if not isinstance(answer[key],str) or not answer[key].strip():
            raise ValueError('Missing submission field: '+key)
    if len(answer['submission_id']) > 128 or len(answer['program'].encode('utf-8')) > 200000:
        raise ValueError('Submission ID or source exceeds local intake limit')
    for key in ('student_id','question_id','question_sha256'):
        if answer[key] != assignment[key]:
            raise ValueError('Submission '+key+' does not match generated question')
    if answer['origin'] not in ('student','test_fixture'):
        raise ValueError('Declare student or test_fixture origin; reference/simulated answers are not student evidence')
    if answer['submitted_at'] is not None:
        validate_timestamp(answer['submitted_at'])
    return {'schema': 'submission-packet-v1', 'assignment': assignment, 'answer': deepcopy(answer)}


def _receipt(packet, received_at):
    a, q = packet['answer'], packet['assignment']
    # Only the student-visible question enters the existing evidence analyser.
    public = {k: q['question'][k] for k in ('title','statement','input_format','output_format','constraints','samples')}
    question_text = json.dumps(public, ensure_ascii=False)
    record_id = 'submission:' + fingerprint([a['student_id'], a['submission_id']])
    row = {'record_id': record_id, 'student_id': a['student_id'], 'problem_id': q['question_id'],
           'split': 'live' if a['origin']=='student' else 'test_fixture',
           'evidence_origin': 'student_submission' if a['origin']=='student' else 'test_fixture',
           'question': question_text, 'program': a['program'],
           'source': {'file': record_id, 'index': 0, 'sha256': fingerprint(a)},
           'submission': {k: q[k] for k in ('question_id','question_sha256','question_version','generation_run_id','recommendation_id')}}
    row['submission'].update(submission_id=a['submission_id'], received_at=received_at,
        submitted_at=a['submitted_at'], program_sha256=fingerprint(a['program']),
        question_text_sha256=fingerprint(question_text))
    return {'schema': SCHEMA, 'status': 'received', 'packet': deepcopy(packet), 'evidence': row,
            'packet_sha256': fingerprint(packet), 'received_at': received_at,
            'learning_evidence_eligible': a['origin']=='student',
            'execution_check': {'status':'not_run'},
            'limitations': ['来源为本地调用方声明，不是身份认证。',
                '接收不表示题目已批准交付；保留原题待复核状态。',
                '未运行学生程序，无独立判题结论；模型分析须另行显式启动。']}


def receive_submission(store, packet):
    if store.state['identity'].get('packet_sha256') != fingerprint(packet):
        raise ValueError('Submission differs from task identity')
    store.state['status'] = 'running'
    store.commit('submission_started')
    try:
        saved = store.start_step('receive', {'submission_id': packet['answer']['submission_id']})
        if saved is None:
            saved = _receipt(packet, stamp())
            store.succeed_step(saved)
        atomic_json(store.path/'submission.json', saved)
        store.state['status'] = 'completed'
        store.commit('submission_received', learning_evidence_eligible=saved['learning_evidence_eligible'])
        return saved
    except BaseException as exc:
        if store.active_step and store.state['steps'][store.active_step]['status']=='running':
            store.state['steps'][store.active_step]['status']='interrupted'
        store.state['status']='interrupted'
        store.commit('submission_interrupted', error=store.error(exc))
        raise
    finally:
        store.export()


def load_submission_evidence(path):
    path = Path(path)
    state = RunStore.inspect(path, full=True)
    receipt = json.loads((path/'submission.json').read_text(encoding='utf-8'))
    step = state['steps'].get('receive', {})
    if state['status'] != 'completed' or step.get('status') != 'succeeded' or step.get('result') != receipt:
        raise ValueError('Submission receipt is incomplete or changed')
    packet = receipt['packet']
    if state['identity'].get('packet_sha256') != fingerprint(packet) or receipt != _receipt(packet,receipt['received_at']):
        raise ValueError('Submission binding changed')
    if not receipt['learning_evidence_eligible']:
        raise ValueError('Test fixture cannot update a real student profile')
    row = deepcopy(receipt['evidence'])
    validate_learning_evidence(row, row['student_id'])
    return row
