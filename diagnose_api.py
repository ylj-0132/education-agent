"""Bounded diagnostic probes for the observed language anomaly, no automatic retries.

Run --case N to preview, add --execute to send exactly one request.
Each case is persisted once; replay cannot accidentally overwrite a paid result.
"""
import argparse
import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from profiling.api_client import NoRedirect, read_env

FAILURE='b315ecefdd2d6a6bf5a0f31cb6148171faebfaf8cdc14b65855749ab8a90d417.json'
CASES={1:'short_chinese',2:'short_english',3:'original_replay_ascii',
       4:'original_replay_utf8',5:'original_replay_ascii_repeat',6:'short_chinese_repeat'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',required=True,type=int,choices=CASES)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    saved=json.loads((Path('cache/chat/failures')/FAILURE).read_text(encoding='utf-8'))
    original=saved['request']['payload']
    payload=copy.deepcopy(original)
    if args.case in (1,6):
        payload['messages']=[{'role':'user','content':'请只返回一个 JSON 对象，字段 evaluation 的值为“中文测试成功”。不要输出其他内容。'}]
    elif args.case==2:
        payload['messages']=[{'role':'user','content':'Return only one JSON object with the field evaluation set to "English test succeeded". Do not output anything else.'}]
    ascii_wire=args.case!=4
    body=json.dumps(payload,ensure_ascii=ascii_wire).encode('utf-8')
    logical=json.dumps(payload,ensure_ascii=False)
    report={'case':args.case,'name':CASES[args.case],'request_payload':payload,
            'ensure_ascii':ascii_wire,'request_body_sha256':hashlib.sha256(body).hexdigest(),
            'logical_payload_sha256':hashlib.sha256(logical.encode()).hexdigest(),
            'request_cyrillic_characters':sum('\u0400'<=c<='\u052f' for c in logical),
            'local_cache_used':False,'mode':'execute' if args.execute else 'preview'}
    if not args.execute:
        print(json.dumps({k:v for k,v in report.items() if k!='request_payload'}));return
    folder=Path('runs/api-language-diagnostic');folder.mkdir(exist_ok=True,parents=True)
    path=folder/f'case-{args.case}.json'
    if path.exists():raise SystemExit('Case already recorded; no request sent')
    env=read_env('.env');key=env.get('ZZZ_API_KEY','')
    url=env.get('ZZZ_BASE_URL','').rstrip('/')+'/chat/completions'
    if not key or url!=saved['request']['url'] or env.get('ZZZ_MODEL')!=payload['model']:
        raise SystemExit('Configuration differs from failed request or key missing; no request sent')
    report['started_at']=datetime.now(timezone.utc).isoformat()
    report['status']='started'
    # Reserve the case before making the paid request.
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    started=time.monotonic()
    request=Request(url,data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    try:
        with build_opener(NoRedirect).open(request,timeout=60) as response:
            raw=response.read().decode('utf-8')
            report['http_status']=response.status
            report['response_headers']={h:response.headers[h] for h in ('x-request-id','request-id','date','content-type') if h in response.headers}
        report['response']=json.loads(raw)
        reply=report['response'];choice=reply.get('choices',[{}])[0]
        content=choice.get('message',{}).get('content','') or ''
        report['response_cyrillic_characters']=sum('\u0400'<=c<='\u052f' for c in content)
        report['finish_reason']=choice.get('finish_reason')
        try:
            parsed=json.loads(content)
            report['valid_evaluation_json']=isinstance(parsed,dict) and isinstance(parsed.get('evaluation'),str)
        except (ValueError,TypeError):report['valid_evaluation_json']=False
        report['status']='received'
    except HTTPError as exc:
        report['status']='http_error';report['http_status']=exc.code
    except Exception as exc:
        report['status']='transport_or_decode_error';report['error_type']=type(exc).__name__
    finally:
        report['elapsed_seconds']=round(time.monotonic()-started,2)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2).replace(key,'[REDACTED]'),encoding='utf-8')
    response=report.get('response',{})
    print(json.dumps({k:v for k,v in report.items() if k not in ('request_payload','response')},ensure_ascii=False))
    print(json.dumps({'response_id':response.get('id'),'model':response.get('model'),
                      'usage':response.get('usage'),'choices':response.get('choices')},ensure_ascii=False).replace(key,'[REDACTED]'))


if __name__=='__main__':main()
