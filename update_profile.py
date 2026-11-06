"""Append a small batch of real historical submissions; preview by default."""
import argparse
import hashlib
import json
from pathlib import Path
from profiling.api_client import CachedChatClient, read_env, send_chat, model_config
from profiling.run_store import RunStore, RecordedTransport, fingerprint, atomic_json
from profiling.student_profile import PROMPT_VERSION
from profiling.incremental import run_update, validate_update
from submissions.workflow import load_submission_evidence
from profiling.java_execution import attach_verifications


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-profile', type=Path)
    inputs=parser.add_mutually_exclusive_group()
    inputs.add_argument('--records', type=Path)
    inputs.add_argument('--submission-run', type=Path)
    parser.add_argument('--verification-run', type=Path, action='append', default=[])
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--output', type=Path, default=Path('runs/profile-update-001'))
    parser.add_argument('--cache', type=Path, default=Path('cache/profile-update'))
    parser.add_argument('--max-calls', type=int, choices=range(1,13), default=12)
    for flag in ('execute','resume','status','trace'):
        parser.add_argument('--'+flag, action='store_true')
    args = parser.parse_args()
    if args.status or args.trace:
        if args.execute or args.resume:
            parser.error('Inspection cannot be combined with execution')
        print(json.dumps(RunStore.inspect(args.output, full=args.trace), ensure_ascii=False, indent=2))
        return
    if not args.base_profile or not (args.records or args.submission_run):
        parser.error('--base-profile and either --records or --submission-run are required')
    if args.resume and not args.execute:
        parser.error('--resume requires --execute')
    base, catalog = [json.loads(p.read_text(encoding='utf-8-sig')) for p in (args.base_profile,args.catalog)]
    rows = ([load_submission_evidence(args.submission_run)] if args.submission_run
            else json.loads(args.records.read_text(encoding='utf-8-sig')))
    rows = attach_verifications(rows,args.verification_run)
    validate_update(base, rows, catalog)
    if len(rows)>5:
        parser.error('This pilot entry accepts at most five new submissions')
    if args.output.resolve() == args.base_profile.resolve().parent:
        parser.error('Use a separate output directory; preserve the base profile')
    print(json.dumps({'mode':'execute' if args.execute else 'preview', 'student_id':base['student_id'],
        'new_records':len(rows), 'problem_ids':[r['problem_id'] for r in rows],
        'request_limit_including_retries':args.max_calls}, ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        return
    settings = read_env(args.env_file)
    limit = min(args.max_calls, int(settings.get('ZZZ_MAX_CALLS','12')))
    client = CachedChatClient(base_url=settings.get('ZZZ_BASE_URL',''),api_key=settings.get('ZZZ_API_KEY',''),
        **model_config(settings, profiling=True),cache_dir=args.cache,max_calls=limit,
        max_tokens=int(settings.get('ZZZ_MAX_TOKENS','1500')),timeout=int(settings.get('ZZZ_TIMEOUT','60')))
    root = Path(__file__).resolve().parent
    files = [Path(__file__).resolve(),*sorted((root/'profiling').glob('*.py')),*sorted((root/'recommendation').glob('*.py')),
             *sorted((root/'submissions').glob('*.py')),root/'verify_java.py',root/'generation/workflow.py']
    identity = {'workflow':'profile-update-v1','base_sha256':fingerprint(base),'records_sha256':fingerprint(rows),
        'catalog_sha256':fingerprint(catalog),'prompt_version':PROMPT_VERSION,
        'model':client.model,'stage_models':client.stage_models,'url':client.url,'max_tokens':client.max_tokens,'timeout':client.timeout,
        'code':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with RunStore(args.output,identity,limit,resume=args.resume,secret=client.key) as store:
        client.transport = RecordedTransport(store,send_chat)
        client.event_sink = store.api_event
        atomic_json(args.output/'base-profile.json',base)
        atomic_json(args.output/'new-evidence.json',rows)
        report = run_update(store,client,base,rows,catalog)
        print(json.dumps({'status':report['status'],'affected_concept_ids':report['revision']['affected_concept_ids'],
                          'failed_steps':report['failed_steps']},ensure_ascii=False,indent=2))
        if report['status']!='completed':
            raise RuntimeError('Update partial; inspect --status/--trace. Base profile remains unchanged')


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,OSError) as exc:
        raise SystemExit(str(exc)) from None
