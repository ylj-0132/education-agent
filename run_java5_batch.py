"""Audited full Java_5 import/profile batch. Preview default; never execute Java.

Bulk execution has its own explicitly supplied quotas, not the pilot .env ceiling.
Existing run quotas and the small-batch CLI are never changed.
"""
import argparse
import hashlib
import json
from pathlib import Path
from profiling.api_client import CachedChatClient, model_config, read_env, send_chat
from profiling.dataset_store import DatasetStore, verified_records
from profiling.reliable_runner import run_profile
from profiling.run_store import RunStore, RecordedTransport, atomic_json, fingerprint
from profiling.student_profile import PROMPT_VERSION

ROOT = Path(__file__).resolve().parent


def execute_batch(output, records, catalog, settings, per_student, total, cache, *, resume=False):
    students = sorted({r['student_id'] for r in records if r['split']=='train'})
    if per_student < 1 or total != per_student*len(students):
        raise ValueError('Batch budget must equal student count times per-student ceiling')
    if per_student < max(sum(r['student_id']==s and r['split']=='train' for r in records) for s in students)+len(catalog['concepts']):
        raise ValueError('Per-student ceiling cannot cover all extraction and possible summary steps')
    params = dict(base_url=settings.get('ZZZ_BASE_URL',''),api_key=settings.get('ZZZ_API_KEY',''),
                  **model_config(settings,profiling=True),cache_dir=cache,max_calls=per_student,
                  max_tokens=int(settings.get('ZZZ_MAX_TOKENS','1500')),timeout=int(settings.get('ZZZ_TIMEOUT','60')))
    # Validate credentials/settings before creating checkpoints.
    CachedChatClient(**params)
    files = [Path(__file__).resolve(), ROOT/'java5_data.py', *sorted((ROOT/'profiling').glob('*.py'))]
    identity = {'workflow':'java5-full-batch-v1','dataset_sha256':fingerprint(records),'catalog_sha256':fingerprint(catalog),
                'prompt_version':PROMPT_VERSION,'model':params['model'],'stage_models':params['stage_models'],
                'url':params['base_url'],'max_tokens':params['max_tokens'],'timeout':params['timeout'],
                'per_student_max_calls':per_student,'batch_max_calls':total,
                'code':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    checkpoint = output/'batch-checkpoint'
    with RunStore(checkpoint,identity,total,resume=resume,secret=params['api_key']) as batch:
        batch.state['status']='running';batch.commit('batch_started')
        with DatasetStore(output/'dataset.sqlite3',records) as db:
            try:
                for student in students:
                    rows = [r for r in records if r['student_id']==student and r['split']=='train']
                    path = output/student
                    student_identity = dict(identity,student_id=student,records_sha256=fingerprint(rows))
                    has_checkpoint = (path/'checkpoint.sqlite3').exists()
                    client = CachedChatClient(**params)
                    with RunStore(path,student_identity,per_student,resume=has_checkpoint and resume,secret=client.key) as store:
                        client.transport = RecordedTransport(store,send_chat)
                        client.event_sink = store.api_event
                        atomic_json(path/'records.json',rows)
                        profile = run_profile(store,client,rows,catalog['concepts'],client.model)
                    if profile['status']!='completed':
                        raise RuntimeError('Student profile incomplete: '+student+'; batch stopped, inspect checkpoint before resuming')
                    db.publish(profile,path)
                    snapshot = db.snapshot()
                    batch.state['completed_students']=snapshot['completed_students']
                    batch.commit('student_registered',student_id=student)
                    atomic_json(output/'batch-report.json',snapshot|{'status':'running','budget':total})
                batch.state['status']='completed';batch.commit('batch_completed')
            except BaseException as exc:
                batch.state['status']='partial';batch.commit('batch_stopped',error=batch.error(exc))
                atomic_json(output/'batch-report.json',db.snapshot()|{'status':'partial','budget':total})
                raise
            finally:
                batch.export()
            reports=[]
            for student in students:
                state=RunStore.inspect(output/student,full=True)
                reports.append({'student_id':student,'profile':(Path(student)/'profile.json').as_posix(),
                                'requests':len(state['attempts']),'status':state['status']})
            result=db.snapshot()|{'status':'completed','budget':total,'student_runs':reports,
                                 'actual_requests':sum(r['requests'] for r in reports),'java_executions':0,
                                 'test_records_analyzed':0,'cost_cny':None}
            atomic_json(output/'batch-report.json',result)
            return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--source-manifest',type=Path,default=ROOT/'artifacts/java5-initial/manifest.json')
    parser.add_argument('--catalog',type=Path,default=ROOT/'config/java-basics.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--cache',type=Path,default=ROOT/'cache/chat')
    parser.add_argument('--max-calls-per-student',type=int)
    parser.add_argument('--batch-max-calls',type=int)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--import-only',action='store_true')
    mode.add_argument('--execute',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    if args.resume and not args.execute: parser.error('--resume requires --execute')
    if args.execute and (not args.max_calls_per_student or not args.batch_max_calls):
        parser.error('Full batch requires explicit --max-calls-per-student and --batch-max-calls quotas')
    records=verified_records(args.data_root,args.source_manifest)
    catalog=json.loads(args.catalog.read_text(encoding='utf-8'))
    students=sorted({r['student_id'] for r in records})
    counts={split:sum(r['split']==split for r in records) for split in ('train','test')}
    preview={'mode':'execute' if args.execute else 'import' if args.import_only else 'preview',
             'students':{s:sum(r['student_id']==s and r['split']=='train' for r in records) for s in students},
             'counts':counts,'maximum_logical_requests':counts['train']+len(students)*len(catalog['concepts']),
             'test_used_for_profile':False,'java_execution':False,'dataset_sha256':fingerprint(records)}
    print(json.dumps(preview,ensure_ascii=False,indent=2),flush=True)
    if not args.execute and not args.import_only:return
    output=args.output.resolve()
    if not output.is_relative_to(ROOT/'runs'):
        raise ValueError('Use a new directory under repository runs for the full dataset')
    if args.import_only:
        with DatasetStore(output/'dataset.sqlite3',records) as db:
            atomic_json(output/'import-report.json',db.snapshot()|{'status':'imported','model_calls':0})
        return
    settings=read_env(args.env_file)
    result=execute_batch(output,records,catalog,settings,args.max_calls_per_student,args.batch_max_calls,args.cache,resume=args.resume)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as exc:raise SystemExit(str(exc)) from None
