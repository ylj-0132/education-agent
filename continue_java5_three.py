"""Explicit three-student continuation; preserve old runs and cumulative attempts.

The rejected summary remains incomplete. No content-filter bypass or extra retry.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from profiling.api_client import CachedChatClient, read_env, model_config, send_chat
from profiling.dataset_store import DatasetStore, verified_records
from profiling.run_store import RunStore, RecordedTransport, atomic_json, fingerprint
from profiling.reliable_runner import run_profile

ROOT=Path(__file__).resolve().parent
BATCH=ROOT/'runs/java5-full-20261103'
STUDENTS=['java_u344869639','java_u520480375','java_u861445290']


def inherit(store, source, diagnostic):
    attempts=copy.deepcopy(source['attempts'])
    if diagnostic:
        for a in copy.deepcopy(diagnostic['attempts']):
            matching=[x for x in attempts if x['step_id']==a['step_id'] and x['request_key']==a['request_key']]
            a['number']=len(matching)+1
            a['inherited_from_run']=diagnostic['run_id']
            attempts.append(a)
    if any(a['status'] in ('sent','unknown') for a in attempts):
        raise ValueError('Cannot migrate unknown request outcome')
    if len(attempts)>store.max_calls:
        raise ValueError('Inherited attempts exceed budget')
    store.state['steps']=copy.deepcopy(source['steps'])
    store.state['attempts']=attempts
    store.state['inherited_from_run']=source['run_id']
    store.commit('explicit_continuation_imported',source_run=source['run_id'],attempts=len(attempts))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    records=verified_records(ROOT/'../paper-screening/data-audit-20261025/student_sim',ROOT/'artifacts/java5-initial/manifest.json')
    catalog=json.loads((ROOT/'config/java-basics.json').read_text(encoding='utf-8'))
    source=RunStore.inspect(BATCH/STUDENTS[1],full=True)
    diagnostic=RunStore.inspect(BATCH/'account-retry-01',full=True)
    first=RunStore.inspect(BATCH/STUDENTS[0],full=True)
    legacy=source['identity']
    for filename,digest in legacy['code'].items():
        if hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()!=digest:
            raise ValueError('Original implementation changed: '+filename)
    assert fingerprint(records)==legacy['dataset_sha256']
    assert fingerprint(catalog)==legacy['catalog_sha256']
    assert first['status']=='completed' and len(first['attempts'])==73
    assert len(source['attempts'])==52 and len(diagnostic['attempts'])==1
    assert source['blocked_reason']['http_status']==402
    assert diagnostic['identity']['source_run']==source['run_id']
    assert diagnostic['attempts'][0]['request']==source['attempts'][-1]['request']
    assert diagnostic['attempts'][0]['status']=='failed'
    print(json.dumps({'students':STUDENTS,'prior_requests':126,'ceiling':426,
                      'blocked_summary_retained':True,'execute':args.execute}),flush=True)
    if not args.execute:return
    settings=read_env(ROOT/'.env')
    params=dict(base_url=settings['ZZZ_BASE_URL'],api_key=settings['ZZZ_API_KEY'],
                **model_config(settings,profiling=True),cache_dir=ROOT/'cache/chat',max_calls=142,
                max_tokens=int(settings.get('ZZZ_MAX_TOKENS','1500')),timeout=int(settings.get('ZZZ_TIMEOUT','60')))
    for old,new in [('url','base_url'),('model','model'),('stage_models','stage_models'),('max_tokens','max_tokens'),('timeout','timeout')]:
        if legacy[old]!=params[new]:raise ValueError('Configuration changed: '+old)
    out=BATCH/'continuation-three'
    reports=[{'student_id':STUDENTS[0],'status':'completed','requests':73,'path':str(BATCH/STUDENTS[0])}]
    with DatasetStore(BATCH/'dataset.sqlite3',records) as db:
        for student in STUDENTS[1:]:
            rows=[r for r in records if r['student_id']==student and r['split']=='train']
            path=out/student
            identity={'workflow':'explicit-three-student-continuation-v1','student_id':student,
                      'records_sha256':fingerprint(rows),'legacy_identity':legacy,
                      'source_state_sha256':fingerprint(source),'diagnostic_state_sha256':fingerprint(diagnostic),
                      'continuation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      'target_students':STUDENTS,'target_max_calls':426,'student_max_calls':142}
            client=CachedChatClient(**params)
            resume=args.resume and (path/'checkpoint.sqlite3').exists()
            with RunStore(path,identity,142,resume=resume,secret=client.key) as store:
                if not resume and student==STUDENTS[1]:inherit(store,source,diagnostic)
                client.transport=RecordedTransport(store,send_chat)
                client.event_sink=store.api_event
                atomic_json(path/'records.json',rows)
                profile=run_profile(store,client,rows,catalog['concepts'],client.model)
                requests=len(store.state['attempts'])
                newly_blocked=bool(store.state.get('blocked_reason'))
            if profile['status']=='completed':db.publish(profile,path)
            reports.append({'student_id':student,'status':profile['status'],'requests':requests,
                            'path':str(path),'failed_steps':profile['failed_steps']})
            result={'status':'partial' if any(r['status']!='completed' for r in reports) else 'completed',
                    'student_runs':reports,'actual_requests':sum(r['requests'] for r in reports),
                    'limit':426,'dataset':db.snapshot(),'java_executions':0,'test_records_analyzed':0}
            atomic_json(out/'report.json',result)
            if newly_blocked:raise RuntimeError('New endpoint/account block; stopped before next student')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
