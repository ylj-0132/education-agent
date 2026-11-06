"""Local CLI adapter. No model or Java work is performed at import time."""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import uuid
from pathlib import Path

SCRIPTS = dict(profile='run_profile.py', recommend='run_recommendation.py', generate='generate_question.py', update='update_profile.py', submit='submit_answer.py', verify_reference='verify_java.py', verify_student='verify_submission.py')
FIELDS = {
 'profile': 'records concepts student verification_run limit max_calls',
 'recommend': 'catalog profile usage_history student scope goals requirement max_calls',
 'generate': 'recommendation_run catalog profile usage_history student scope targets requirement max_calls',
 'update': 'base_profile records submission_run verification_run catalog max_calls',
 'submit': 'generation_run submission',
 'verify_reference': 'generation_run cases approve_sha256',
 'verify_student': 'records submission_run record_id cases approve_sha256',
}
ARRAYS = {'scope','targets','goals','requirement','verification_run'}
PATHS = {'records','concepts','catalog','profile','usage_history','recommendation_run','base_profile','submission_run','generation_run','submission','cases','verification_run'}
KINDS = {'profile.json':'profiles','generation.json':'generations','recommendation.json':'recommendations','verification.json':'verification_runs','submission.json':'submission_runs'}
LIMIT = 24000

def scrub(value):
    text = str(value)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s"\r\n]+',r'\1[redacted]',text)
    text = re.sub(r'(?i)((?:api[_-]?key|token|secret|password)\s*["\']?\s*[:=]\s*["\']?)[^\s,"\'\r\n]+',r'\1[redacted]',text)
    return re.sub(r'\bsk-[A-Za-z0-9_-]+','[redacted]',text)[:LIMIT]

def run_command(args, cwd):
    environment=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
    process=subprocess.Popen(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,env=environment)
    buffers=[bytearray(),bytearray()]
    def drain(stream,buffer):
        with stream:
            while True:
                chunk=stream.read(4096)
                if not chunk:break
                # Continue draining after the visible capture limit to avoid deadlock.
                buffer.extend(chunk[:max(0,LIMIT*4-len(buffer))])
    readers=[threading.Thread(target=drain,args=(stream,buffer),daemon=True) for stream,buffer in zip((process.stdout,process.stderr),buffers)]
    for reader in readers:reader.start()
    try:code=process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        process.kill();process.wait();code=124
    for reader in readers:reader.join(timeout=2)
    return code,*(bytes(b).decode('utf-8',errors='replace') for b in buffers)

class WorkbenchService:
    def __init__(self,root,runner=None):
        self.root=Path(root).resolve()
        self.runner=runner or run_command
        self.csrf_token=secrets.token_urlsafe(32)
        self._jobs={};self._lock=threading.Lock();self._busy=False

    def safe_path(self,value):
        if not isinstance(value,str) or not value or '\\' in value or ':' in value or Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError('Only repository relative paths are accepted')
        path=(self.root/value).resolve()
        if not path.is_relative_to(self.root) or any(p.startswith('.') for p in Path(value).parts):
            raise ValueError('Path is outside allowed repository data')
        return path

    def inventory(self):
        groups={key:[] for key in ['profiles','generations','recommendations','record_files','usage_files','submission_runs','verification_runs']}
        allowed={}
        loaded={}
        candidates=[self.root/'config/java-basics.json']
        for folder in ('runs','artifacts'):
            base=self.root/folder
            if base.exists():candidates.extend(base.rglob('*.json'))
        for path in sorted(candidates):
            rel=path.relative_to(self.root).as_posix()
            if path.name in ('trace.json','request.json') or not path.resolve().is_relative_to(self.root):continue
            kind=KINDS.get(path.name)
            if path.parent.name=='usage-history':kind='usage_files'
            if path.name in ('records.json','new-evidence.json','review-batch.json','train-evidence.json'):kind='record_files'
            if not kind and rel!='config/java-basics.json':continue
            try:
                if path.stat().st_size>8_000_000:continue
                data=json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError,ValueError):continue
            if isinstance(data,dict) and data.get('schema')=='question-usage-sources-v1':kind='usage_files'
            elif kind=='usage_files':continue
            if kind=='record_files' and (not isinstance(data,list) or not data or any(not isinstance(row,dict) or not row.get('question') or not row.get('program') for row in data)):
                continue
            allowed[rel]=path
            loaded[rel]=data
            if kind:
                info=data if isinstance(data,dict) else {}
                groups[kind].append(dict(path=rel,student_id=info.get('student_id'),status=info.get('status',info.get('execution_status','available')),label=path.parent.name))
        generation_students={}
        for item in groups['generations']:
            info=loaded[item['path']]
            if info.get('question_id') and item['student_id']:
                generation_students.setdefault(info['question_id'],set()).add(item['student_id'])
        for item in groups['submission_runs']:
            info=loaded[item['path']]
            packet=info.get('packet',{})
            evidence=info.get('evidence',{})
            answer=packet.get('answer',{})
            identities={x for x in (evidence.get('student_id'),answer.get('student_id'),packet.get('assignment',{}).get('student_id')) if isinstance(x,str) and x}
            item['student_id']=next(iter(identities)) if len(identities)==1 else None
            item['learning_evidence_eligible']=info.get('learning_evidence_eligible') is True and answer.get('origin')=='student' and bool(item['student_id'])
        for item in groups['verification_runs']:
            path=allowed[item['path']].parent/'approval.json'
            approval={}
            try:
                if path.resolve().is_relative_to(self.root) and path.stat().st_size<=8_000_000:
                    approval=json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError,ValueError):pass
            question=approval.get('question',{}) if isinstance(approval,dict) else {}
            evidence=question.get('student_evidence') if isinstance(question,dict) else None
            if isinstance(evidence,dict):
                item['verification_kind']='student'
                item['student_id']=evidence.get('student_id')
            else:
                item['verification_kind']='reference'
                students=generation_students.get(loaded[item['path']].get('question_id'),set())
                item['student_id']=next(iter(students)) if len(students)==1 else None
        for item in groups['record_files']:
            students=sorted({row['student_id'] for row in loaded[item['path']] if isinstance(row.get('student_id'),str) and row['student_id']})
            item['student_ids']=students
            item['student_id']=students[0] if len(students)==1 else None
        for items in groups.values():
            for item in items:item['identity_verified']=bool(item['student_id'])
        return groups,allowed

    def artifact(self,value):
        self.safe_path(value)
        _,allowed=self.inventory()
        if value not in allowed:raise ValueError('Artifact is not in the public result allowlist')
        return json.loads(allowed[value].read_text(encoding='utf-8-sig'))

    def bootstrap(self):
        groups,_=self.inventory()
        catalog=self.artifact('config/java-basics.json') if (self.root/'config/java-basics.json').exists() else {}
        sources=dict(profile='profiles',base_profile='profiles',generation_run='generations',recommendation_run='recommendations',submission_run='submission_runs',verification_run='verification_runs',records='record_files',usage_history='usage_files')
        operations=[dict(id=k,label=k,fields=[dict(name=f,label=f,type='path' if f in PATHS else 'text',source=sources.get(f),required=False,multiple=f in ARRAYS) for f in v.split()],modes=['preview','save' if k=='submit' else 'execute']) for k,v in FIELDS.items()]
        return dict(csrf_token=self.csrf_token,catalog=catalog,operations=operations,**groups)

    def command(self,data,job_id):
        op=data.get('operation');mode=data.get('mode');fields=data.get('fields',{})
        if op not in SCRIPTS or mode not in ('preview','save' if op=='submit' else 'execute'):raise ValueError('Unknown operation or mode')
        if not isinstance(fields,dict) or set(fields)-set(FIELDS[op].split()):raise ValueError('Unsupported fields')
        if mode!='preview' and data.get('confirm') is not True:raise ValueError('Explicit confirm=true is required')
        approval=data.get('approve_sha256',fields.get('approve_sha256'))
        if approval and (not isinstance(approval,str) or not re.fullmatch('[0-9a-fA-F]{64}',approval)):
            raise ValueError('Invalid approval SHA256')
        if op.startswith('verify_') and mode=='execute' and (not isinstance(approval,str) or not re.fullmatch('[0-9a-fA-F]{64}',approval)):
            raise ValueError('Review source and cases, then provide approve_sha256')
        args=[sys.executable,str(self.root/SCRIPTS[op])]
        if op.startswith('verify_'):
            args.extend(['--java-home',os.environ.get('LOCAL_JAVA_TEST_HOME','C:/Program Files/JetBrains/PyCharm 2024.3.4/jbr')])
        for key,value in fields.items():
            if value=='' or value==[]:continue
            if key in ARRAYS:
                if not isinstance(value,list) or len(value)>100 or any(not isinstance(x,str) or not x or len(x)>4000 for x in value):raise ValueError('Invalid array field')
                values=value
                if key!='requirement' and any(x.startswith('-') for x in values):raise ValueError('Array values cannot be CLI switches')
            else:
                if not isinstance(value,str) or len(value)>4000 or '\x00' in value:raise ValueError('Fields must be strings')
                values=[value]
            if key in PATHS:
                values=[str(self.safe_path(x)) for x in values]
                if any(not Path(x).exists() for x in values):raise ValueError('Input path does not exist')
                if any(Path(x).name.startswith('.') or Path(x).name in ('trace.json','request.json') for x in values):raise ValueError('Private input forbidden')
                for x in values:
                    p=Path(x);rel=p.relative_to(self.root)
                    if not rel.parts or rel.parts[0] not in ('runs','artifacts','config') or (p.is_file() and p.suffix!='.json'):
                        raise ValueError('Only JSON data and run directories may be used as inputs')
                    if key.endswith('_run') and p.is_file():
                        values[values.index(x)]=str(p.parent)
            flag='--'+key.replace('_','-')
            if key in ('requirement','verification_run'):
                for x in values:args.append(flag+'='+x)
            elif key not in ARRAYS and values[0].startswith('-'):args.append(flag+'='+values[0])
            else:args.extend([flag,*values])
        if approval and 'approve_sha256' not in fields:args.extend(['--approve-sha256',approval])
        output=self.root/'runs'/('workbench-'+job_id)
        args.extend(['--output',str(output)])
        if mode!='preview':args.append('--save' if mode=='save' else '--execute')
        return args,output.relative_to(self.root).as_posix()

    def launch(self,data):
        if not isinstance(data,dict):raise ValueError('JSON object required')
        job_id=uuid.uuid4().hex
        args,output=self.command(data,job_id)
        with self._lock:
            if self._busy:raise RuntimeError('Another task is running')
            self._busy=True
            job=dict(id=job_id,status='queued',operation=data['operation'],mode=data['mode'],output=output,log='',error=None)
            self._jobs[job_id]=job
            # Keep memory finite; active job is always retained.
            while len(self._jobs)>50:self._jobs.pop(next(iter(self._jobs)))
        def work():
            try:
                with self._lock:job['status']='running'
                code,out,err=self.runner(args,self.root)
                with self._lock:
                    job.update(status='succeeded' if code==0 else 'failed',log=scrub(out),error=scrub(err) or (f'CLI exited with status {code}' if code else None))
            except Exception:
                with self._lock:job.update(status='failed',error='Local task failed; inspect local terminal and result files')
            finally:
                with self._lock:self._busy=False
        threading.Thread(target=work,daemon=True).start()
        return self.get_job(job_id)

    def get_job(self,job_id):
        with self._lock:
            if job_id not in self._jobs:raise KeyError('Unknown job')
            return dict(self._jobs[job_id])

    def jobs(self):
        with self._lock:return [dict(x) for x in reversed(list(self._jobs.values()))]

    def save_submission_draft(self,answer):
        if not isinstance(answer,dict) or answer.get('schema')!='student-answer-v1' or answer.get('origin') not in ('student','test_fixture'):
            raise ValueError('Expected student-answer-v1 with explicit student/test_fixture origin')
        for field in ('submission_id','student_id','question_id','question_sha256','program'):
            if not isinstance(answer.get(field),str) or not answer[field]:raise ValueError('Missing answer field: '+field)
        if not re.fullmatch('[0-9a-fA-F]{64}',answer['question_sha256']):raise ValueError('Invalid question SHA256')
        content=json.dumps(answer,ensure_ascii=False,indent=2)
        if len(content.encode('utf-8'))>60000:raise ValueError('Answer draft too large')
        folder=self.safe_path('artifacts/workbench-inputs')
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/(uuid.uuid4().hex+'.json')
        with path.open('x',encoding='utf-8') as handle:handle.write(content)
        return path.relative_to(self.root).as_posix()
