"""Preview student Java source and independent cases; execute only with exact hash approval."""
import argparse
import hashlib
import json
from pathlib import Path
from generation.local_java import verify_local
from profiling.java_execution import prepare_student_review
from profiling.run_store import RunStore
from submissions.workflow import load_submission_evidence


def main():
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group()
    source.add_argument('--records',type=Path)
    source.add_argument('--submission-run',type=Path)
    p.add_argument('--record-id')
    p.add_argument('--cases',type=Path)
    p.add_argument('--java-home',type=Path)
    p.add_argument('--output',type=Path,default=Path('runs/student-java-001'))
    p.add_argument('--approve-sha256')
    for flag in ('execute','resume','status','trace'):p.add_argument('--'+flag,action='store_true')
    args=p.parse_args()
    if args.status or args.trace:
        if args.execute or args.resume:p.error('Inspection cannot be combined with execution')
        print(json.dumps(RunStore.inspect(args.output,full=args.trace),ensure_ascii=False,indent=2));return
    if not (args.records or args.submission_run) or not args.cases or not args.java_home:
        p.error('Provide records/submission-run, cases and java-home')
    if args.resume and not args.execute:p.error('--resume requires --execute')
    if args.execute and not args.approve_sha256:p.error('Review exact source and cases; --approve-sha256 required')
    if args.submission_run:
        if args.record_id:p.error('--record-id only applies to --records')
        row=load_submission_evidence(args.submission_run)
    else:
        rows=json.loads(args.records.read_text(encoding='utf-8-sig'))
        if args.record_id:rows=[r for r in rows if r['record_id']==args.record_id]
        if len(rows)!=1:p.error('Select exactly one record using --record-id')
        row=rows[0]
    cases=json.loads(args.cases.read_text(encoding='utf-8-sig'))
    tools={k:args.java_home/'bin'/(k+'.exe') for k in ('java','javac')}
    package=prepare_student_review(row,cases,tools)
    print(json.dumps({'mode':'execute' if args.execute else 'preview','source_kind':'student_submission',
        'review':package,'notice':'Reviewed local execution, not a filesystem/network sandbox. No API calls or profile updates.'},ensure_ascii=False,indent=2),flush=True)
    if not args.execute:return
    root=Path(__file__).resolve().parent
    files=[Path(__file__).resolve(),root/'generation/local_java.py',root/'profiling/java_execution.py',
        root/'profiling/evidence.py',root/'profiling/run_store.py',root/'submissions/workflow.py']
    identity={'workflow':'student-java-v1','approval_sha256':package['approval_sha256'],
        'code':{str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
    with RunStore(args.output,identity,0,resume=args.resume) as store:
        result=verify_local(store,package,args.approve_sha256)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if result['status'] not in ('compile_failed','tests_failed','tests_passed'):
            raise RuntimeError('Execution incomplete; inspect trace')


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,KeyError) as exc:raise SystemExit(str(exc)) from None
