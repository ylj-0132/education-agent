"""Local transactional checkpoints and traces. No network on import."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import traceback
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from .retry_policy import retryable_transport


def stamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def atomic_json(path,value):
    path=Path(path)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('w',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()


class RunStore:
    def __init__(self,path,identity,max_calls,*,resume=False,secret=''):
        self.path=Path(path);self.secret=secret;self.active_step=None;self.active_attempt=None
        self.db=None;self.lock=None
        if resume:
            if not (self.path/'checkpoint.sqlite3').is_file():
                raise ValueError('No checkpoint; legacy run directories cannot be resumed')
        else:self.path.mkdir(parents=True,exist_ok=False)
        try:
            self.lock=(self.path/'run.lock').open('a+b')
            self.lock.seek(0);self.lock.write(b'0');self.lock.flush();self.lock.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            if self.lock:self.lock.close()
            raise RuntimeError('Task is already running; concurrent resume refused') from None
        try:
            self.db=sqlite3.connect(self.path/'checkpoint.sqlite3')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)')
            row=self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
            if resume:
                if not row:raise ValueError('Incomplete checkpoint initialization')
                self.state=json.loads(row[0])
                if self.state['identity']!=identity or self.state['max_calls']!=max_calls:
                    raise ValueError('Resume input/config/version/budget mismatch; do not reuse this task')
                for a in self.state['attempts']:
                    if a['status']=='sent':a['status']='unknown'
                for s in self.state['steps'].values():
                    if s['status']=='running':s['status']='interrupted'
            else:
                self.state={'schema':1,'run_id':uuid.uuid4().hex,'identity':identity,'max_calls':max_calls,
                            'status':'created','steps':{},'attempts':[]}
            self.max_calls=max_calls
            self.commit('run_resumed' if resume else 'run_created')
        except BaseException:
            self.close();raise

    def __enter__(self):return self

    def __exit__(self,*args):self.close()

    def close(self):
        if self.db:self.db.close();self.db=None
        if self.lock:self.lock.close();self.lock=None

    def clean(self,value):
        if isinstance(value,str):return value.replace(self.secret,'[REDACTED]') if self.secret else value
        if isinstance(value,dict):return {k:self.clean(v) for k,v in value.items()}
        if isinstance(value,list):return [self.clean(v) for v in value]
        return value

    def commit(self,event,**detail):
        item=self.clean({'time':stamp(),'run_id':self.state['run_id'],'step_id':self.active_step,
                         'event':event,**detail})
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(json.dumps(self.clean(self.state),ensure_ascii=False),))
            self.db.execute('INSERT INTO events(body) VALUES (?)',(json.dumps(item,ensure_ascii=False),))

    def error(self,exc):
        chain=[];seen=set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            chain.append({'type':type(exc).__name__,'message':str(exc),
                          'reason':str(getattr(exc,'reason','')),'errno':getattr(exc,'errno',None),
                          'frames':[{'file':f.filename,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(exc.__traceback__)]})
            exc=exc.__cause__ or exc.__context__
        return self.clean(chain)

    def start_step(self,step_id,metadata):
        self.active_step=step_id;self.active_attempt=None
        old=self.state['steps'].get(step_id)
        if old and old['status']=='succeeded':return old['result']
        self.state['steps'][step_id]={'status':'running','metadata':metadata,'started_at':stamp()}
        self.commit('step_started',metadata=metadata)
        return None

    def plan_steps(self,steps):
        for key,metadata in steps.items():
            self.state['steps'].setdefault(key,{'status':'pending','metadata':metadata})
        self.commit('steps_planned',step_ids=list(steps))

    def succeed_step(self,result):
        self.state['steps'][self.active_step].update(status='succeeded',result=result,finished_at=stamp())
        self.commit('step_succeeded')

    def fail_step(self,exc):
        self.state['steps'][self.active_step].update(status='failed',error=self.error(exc),finished_at=stamp())
        self.commit('step_failed',error=self.error(exc))

    def reserve(self,url,payload):
        self.active_attempt=None
        request={'url':url,'payload':payload};key=fingerprint(request)
        matching=[a for a in self.state['attempts'] if a['step_id']==self.active_step and a['request_key']==key]
        if matching:
            latest=matching[-1]
            if latest['status'] in ('received','accepted'):
                self.active_attempt=latest['id'];self.commit('response_replayed',attempt_id=latest['id'])
                return latest,True
            if latest['status'] in ('sent','unknown'):
                raise RuntimeError('Request outcome unknown; inspect trace before any explicit new task')
            if not latest.get('retryable') or len(matching)>=2:
                raise RuntimeError('Step retry limit reached or error is not retryable')
        if self.state.get('blocked_reason'):
            raise RuntimeError('Task paused by endpoint/account error; inspect trace before a new task')
        if len(self.state['attempts'])>=self.max_calls:
            raise RuntimeError('Task request limit reached; resume does not reset budget')
        a={'id':uuid.uuid4().hex,'step_id':self.active_step,'request_key':key,'request':request,
           'number':len(matching)+1,'status':'sent','started_at':stamp(),'usage':None}
        self.state['attempts'].append(a);self.active_attempt=a['id']
        self.commit('request_started',attempt_id=a['id'],number=a['number'],request_key=key)
        return a,False

    def api_event(self,event):
        if self.active_attempt:
            a=next(a for a in self.state['attempts'] if a['id']==self.active_attempt)
            if a['status'] in ('received','accepted'):
                a['status']='accepted' if event['status']=='ok' else 'rejected'
                a['retryable']=event.get('finish_reason') in (None,'stop')
        self.commit('api_'+event['status'],detail=event,attempt_id=self.active_attempt)

    def snapshot(self):
        return self.clean(self.state)|{'requests_reserved':len(self.state['attempts']),
            'events':[json.loads(row[0]) for row in self.db.execute('SELECT body FROM events ORDER BY seq')]}

    def export(self):
        snap=self.snapshot()
        atomic_json(self.path/'trace.json',snap)
        atomic_json(self.path/'run.json',{'run_id':snap['run_id'],'status':snap['status'],
            'requests_reserved':snap['requests_reserved'],'request_limit':self.max_calls,
            'steps':{k:{x:v[x] for x in ('status','metadata') if x in v} for k,v in snap['steps'].items()}})

    @staticmethod
    def inspect(path,*,full=False):
        dbpath=Path(path).resolve()/'checkpoint.sqlite3'
        if not dbpath.is_file():raise ValueError('No checkpoint in this directory')
        db=sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True)
        try:
            db.execute('BEGIN')
            row=db.execute('SELECT body FROM state WHERE id=1').fetchone()
            if not row:raise ValueError('Incomplete checkpoint initialization')
            state=json.loads(row[0])
            if full:
                return state|{'requests_reserved':len(state['attempts']),
                    'events':[json.loads(r[0]) for r in db.execute('SELECT body FROM events ORDER BY seq')]}
            return {'run_id':state['run_id'],'status':state['status'],'request_limit':state['max_calls'],
                    'requests_reserved':len(state['attempts']),
                    'unknown_requests':sum(a['status'] in ('sent','unknown') for a in state['attempts']),
                    'steps':{k:{x:v[x] for x in ('status','metadata') if x in v} for k,v in state['steps'].items()}}
        finally:db.close()


class RecordedTransport:
    def __init__(self,store,transport):self.store,self.transport=store,transport

    def __call__(self,url,key,payload,timeout):
        a,replay=self.store.reserve(url,payload)
        if replay:return a['response']
        started=time.monotonic()
        try:
            response=self.transport(url,key,payload,timeout)
        except Exception as exc:
            a.update(status='failed',retryable=retryable_transport(exc),error=self.store.error(exc),
                     elapsed_seconds=time.monotonic()-started,finished_at=stamp())
            if isinstance(exc,HTTPError) and exc.code in (400,401,402,403,404,429):
                self.store.state['blocked_reason']={'http_status':exc.code,'attempt_id':a['id']}
            self.store.commit('request_failed',attempt_id=a['id'],error=a['error'])
            raise
        a.update(status='received',response=self.store.clean(response),finished_at=stamp(),
                 elapsed_seconds=time.monotonic()-started,
                 usage=response.get('usage') if isinstance(response,dict) else None)
        self.store.commit('response_received',attempt_id=a['id'],usage=a['usage'],
                          response_id=response.get('id') if isinstance(response,dict) else None)
        return response
