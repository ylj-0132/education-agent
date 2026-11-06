"""Preview or save a version-bound answer. Never calls a model or executes Java."""
import argparse
import hashlib
import json
from pathlib import Path
from profiling.run_store import RunStore, fingerprint
from submissions.workflow import load_assignment, prepare_submission, receive_submission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation-run',type=Path)
    parser.add_argument('--submission',type=Path)
    parser.add_argument('--output',type=Path,default=Path('runs/submission-001'))
    for flag in ('save','resume','status','trace'):
        parser.add_argument('--'+flag,action='store_true')
    args=parser.parse_args()
    if args.status or args.trace:
        if args.save or args.resume:parser.error('Inspection cannot be combined with saving')
        print(json.dumps(RunStore.inspect(args.output,full=args.trace),ensure_ascii=False,indent=2));return
    if not args.generation_run:parser.error('--generation-run is required')
    if args.resume and not args.save:parser.error('--resume requires --save')
    if not args.submission:
        if args.save:parser.error('--submission is required to save')
        a=load_assignment(args.generation_run)
        print(json.dumps({'schema':'student-answer-v1','submission_id':'replace-with-unique-id',
            'student_id':a['student_id'],'question_id':a['question_id'],'question_sha256':a['question_sha256'],
            'origin':'test_fixture','program':'','submitted_at':None},ensure_ascii=False,indent=2));return
    answer=json.loads(args.submission.read_text(encoding='utf-8-sig'))
    packet=prepare_submission(args.generation_run,answer)
    print(json.dumps({'mode':'save' if args.save else 'preview','submission_id':answer['submission_id'],
        'student_id':answer['student_id'],'question_id':answer['question_id'],'origin':answer['origin'],
        'learning_evidence_eligible':answer['origin']=='student','generation_status':packet['assignment']['generation_status'],
        'ready_for_student':packet['assignment']['ready_for_student']},ensure_ascii=False,indent=2))
    if not args.save:return
    root=Path(__file__).resolve().parent
    files=[Path(__file__).resolve(),root/'submissions/workflow.py',root/'profiling/evidence.py',
           root/'profiling/run_store.py',root/'verify_java.py',root/'generation/workflow.py']
    identity={'workflow':'submission-intake-v1','packet_sha256':fingerprint(packet),
              'code':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with RunStore(args.output,identity,0,resume=args.resume) as store:
        receipt=receive_submission(store,packet)
        print(json.dumps({'status':receipt['status'],'record_id':receipt['evidence']['record_id'],
                          'received_at':receipt['received_at']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,KeyError) as exc:
        raise SystemExit(str(exc)) from None
