"""Preview by default; --execute explicitly enables the configured paid API."""
import argparse
import hashlib
import json
from pathlib import Path
from profiling.api_client import CachedChatClient, read_env, send_chat, model_config
from profiling.student_profile import PROMPT_VERSION
from profiling.run_store import RunStore, RecordedTransport, fingerprint
from profiling.reliable_runner import run_profile
from profiling.java_execution import attach_verifications


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',type=Path,default=Path('artifacts/java5-initial/review-batch.json'))
    parser.add_argument('--concepts',type=Path,default=Path('config/java-basics.json'))
    parser.add_argument('--student',default='java_u520480375')
    parser.add_argument('--verification-run',type=Path,action='append',default=[])
    parser.add_argument('--limit',type=int,default=3,choices=range(1,6))
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--output',type=Path,default=Path('runs/java5-pilot-001'))
    parser.add_argument('--cache',type=Path,default=Path('cache/chat'))
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--resume',action='store_true',help='Resume the same checkpoint without resetting budget')
    parser.add_argument('--status',action='store_true',help='Inspect persisted progress without reading .env or calling API')
    parser.add_argument('--trace',action='store_true',help='Read full durable trace, including after abrupt process exit')
    parser.add_argument('--max-calls',type=int,choices=range(1,13),help='Optional per-run request ceiling, capped by .env')
    args=parser.parse_args()
    if args.status or args.trace:
        if args.execute or args.resume:parser.error('--status cannot be combined with execution')
        print(json.dumps(RunStore.inspect(args.output,full=args.trace),ensure_ascii=False,indent=2))
        return
    if args.resume and not args.execute:
        parser.error('--resume requires --execute')
    rows=json.loads(args.records.read_text(encoding='utf-8'))
    rows=[r for r in rows if r['student_id']==args.student and r['split']=='train'][:args.limit]
    if not rows:parser.error('No matching training records')
    rows=attach_verifications(rows,args.verification_run)
    config=json.loads(args.concepts.read_text(encoding='utf-8'))
    preview={'student_id':args.student,'records':len(rows),'problem_ids':[r['problem_id'] for r in rows],
             'concept_version':config['version'],'concept_count':len(config['concepts']),
             'maximum_logical_requests':len(rows)+len(config['concepts']),
             'mode':'execute' if args.execute else 'preview'}
    print(json.dumps(preview,ensure_ascii=False,indent=2))
    if not args.execute:return
    if args.output.exists() and not args.resume:parser.error('Output exists; use --resume for a checkpoint or choose a new directory')
    settings=read_env(args.env_file)
    limit=min(args.max_calls or 12,int(settings.get('ZZZ_MAX_CALLS','12')))
    params={'base_url':settings.get('ZZZ_BASE_URL',''),'api_key':settings.get('ZZZ_API_KEY',''),
            **model_config(settings, profiling=True),'cache_dir':args.cache,'max_calls':limit,
            'max_tokens':int(settings.get('ZZZ_MAX_TOKENS','1500')),'timeout':int(settings.get('ZZZ_TIMEOUT','60'))}
    identity={'records_sha256':fingerprint(rows),'concepts_sha256':fingerprint(config),'student_id':args.student,
              'prompt_version':PROMPT_VERSION,'model':params['model'],'stage_models':params['stage_models'],'url':params['base_url'],
              'max_tokens':params['max_tokens'],'timeout':params['timeout'],
              'code':{str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in [Path(__file__),*sorted((Path(__file__).parent/'profiling').glob('*.py'))]}}
    # Validate credentials and limits before creating the persistent task.
    client=CachedChatClient(**params)
    with RunStore(args.output,identity,limit,resume=args.resume,secret=params['api_key']) as store:
        client.transport=RecordedTransport(store,send_chat);client.event_sink=store.api_event
        profile=run_profile(store,client,rows,config['concepts'],client.model)
        print(json.dumps(RunStore.inspect(args.output),ensure_ascii=False,indent=2))
        if profile['status']!='completed':raise RuntimeError('Task partial; inspect --status and trace.json')


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as exc:
        raise SystemExit(str(exc)) from None
