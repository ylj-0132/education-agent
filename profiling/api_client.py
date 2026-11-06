"""Explicit, bounded Chat Completions adapter. No requests at import time."""
import hashlib
import json
import time
import uuid
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .retry_policy import retryable_transport


def read_env(path):
    values={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        key,sep,value=line.partition('=')
        if not sep:raise ValueError('Invalid .env line; expected NAME=value')
        value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'":value=value[1:-1]
        values[key.strip()]=value
    return values


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def model_config(settings, *, profiling=False, generation=False):
    """Explicit stage routing, with compatibility for existing single-model envs."""
    weak = settings.get('ZZZ_WEAK_MODEL') or settings.get('ZZZ_MODEL', '')
    strong = settings.get('ZZZ_STRONG_MODEL') or weak
    stages = {'extract': strong} if profiling else {}
    if generation: stages['review_question'] = strong
    return {'model': weak, 'stage_models': stages}


def send_chat(url,key,payload,timeout):
    request=Request(url,data=json.dumps(payload).encode('utf-8'),
                    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    with build_opener(NoRedirect).open(request,timeout=timeout) as response:
        body=response.read().decode('utf-8')
        try:result=json.loads(body)
        except json.JSONDecodeError:result={'invalid_body':body}
        if isinstance(result,dict):
            result['_http']={'status':response.status,'request_id':response.headers.get('x-request-id') or response.headers.get('request-id')}
        return result


class RetryableResponseError(ValueError):
    pass


class RetryableRequestError(RuntimeError):
    pass


class BusinessValidationError(ValueError):
    pass


def refusal_summary(parsed):
    """Reject obvious service refusals, not ordinary uncertainty about evidence."""
    value=parsed.get('evaluation','') if isinstance(parsed,dict) else ''
    if not isinstance(value,str):return False
    return bool(re.match(r"\s*(抱歉|很抱歉|对不起|本服务不支持|sorry|i(?: am|'m) sorry|i cannot|i can't|извините|я не могу)",value,re.I)
                and re.search(r"不支持|无法|不能|cannot|can't|unable|not support|не могу|не поддерж",value,re.I))


class CachedChatClient:
    def __init__(self,*,base_url,api_key,model,cache_dir,max_calls=12,max_tokens=1500,timeout=60,transport=send_chat,event_sink=None,stage_models=None):
        parts=urlsplit(base_url)
        if parts.scheme!='https' or not parts.netloc or parts.username or parts.query or parts.fragment:
            raise ValueError('Use a plain HTTPS base URL without credentials or query')
        if not api_key.strip() or not model.strip():raise ValueError('Fill ZZZ_API_KEY and ZZZ_MODEL in .env')
        if min(max_calls,max_tokens,timeout)<1:raise ValueError('Limits must be positive')
        self.url=base_url.rstrip('/')+'/chat/completions'
        self.key,self.model=api_key,model
        self.stage_models=dict(stage_models or {})
        if any(k not in ('extract','summarize','recommend_candidates','recommend_select','recommend_direct',
                         'generate_question','review_question') or not isinstance(v,str) or not v.strip()
               for k,v in self.stage_models.items()):
            raise ValueError('Invalid stage model configuration')
        self.cache=Path(cache_dir);self.cache.mkdir(parents=True,exist_ok=True)
        self.max_calls,self.max_tokens,self.timeout=max_calls,max_tokens,timeout
        self.transport=transport;self.events=[];self.calls=0
        self.event_sink=event_sink

    def model_for(self,stage):
        return self.stage_models.get(stage,self.model)

    def emit(self,event):
        if self.event_sink:self.event_sink(event)

    def complete(self,stage,prompt,*,validator=None):
        # One unchanged replay only; every attempt consumes the existing run budget.
        for attempt in (1,2):
            try:
                return self._complete_once(stage,prompt,attempt,validator)
            except (RetryableResponseError,RetryableRequestError):
                if attempt==2 or self.calls>=self.max_calls:raise
                self.events[-1]['retry_scheduled']=True
                self.emit(self.events[-1])
                time.sleep(2)

    def complete_validated(self,stage,prompt,validator):
        """Business validation runs before acceptance, caching or checkpoint success."""
        return self.complete(stage,prompt,validator=validator)

    def _validate_parsed(self,stage,parsed,validator):
        if not isinstance(parsed,dict):raise ValueError('Invalid response object')
        if stage=='summarize':
            if not isinstance(parsed.get('evaluation'),str) or not parsed['evaluation'].strip():
                raise ValueError('Missing evaluation')
            if refusal_summary(parsed):raise ValueError('service_refusal')
        elif stage=='extract':
            if not isinstance(parsed.get('observations'),list):raise ValueError('Missing observations')
            for o in parsed['observations']:
                if not isinstance(o,dict) or o.get('status') not in ('Good','Bad','Unknown') or any(
                    not isinstance(o.get(k),str) or not o[k].strip() for k in ('concept_id','description')):
                    raise ValueError('Invalid observation fields')
        else:
            if not callable(validator):raise ValueError('Recommendation requires a business validator')
            if refusal_summary({'evaluation':parsed.get('reason','')}):raise ValueError('service_refusal')
        if validator:
            try:validator(parsed)
            except ValueError as exc:raise BusinessValidationError(str(exc)) from None

    def _complete_once(self,stage,prompt,attempt,validator=None):
        recommendation_stages=('recommend_candidates','recommend_select','recommend_direct','generate_question','review_question')
        if stage not in ('extract','summarize',*recommendation_stages):raise ValueError('Unknown stage')
        if stage in recommendation_stages and not callable(validator):
            raise ValueError('Recommendation requires a business validator')
        if isinstance(prompt,str):
            messages=[{'role':'user','content':prompt}]
        elif isinstance(prompt,list) and prompt and all(
                isinstance(m,dict) and set(m)=={'role','content'}
                and m['role'] in ('system','user') and isinstance(m['content'],str)
                and m['content'].strip() for m in prompt) and prompt[-1]['role']=='user':
            messages=[dict(m) for m in prompt]
        else:
            raise ValueError('Expected prompt text or system/user message objects ending in user')
        selected_model=self.model_for(stage)
        payload={'model':selected_model,'messages':messages,
                 'max_tokens':self.max_tokens,'stream':False}
        if selected_model in ('gpt-5.6-luna','gpt-5.6-terra'):
            payload['max_completion_tokens']=payload.pop('max_tokens')
            payload['reasoning_effort']='low'
        identity={'adapter':'chat-v1','url':self.url,'stage':stage,'payload':payload}
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        path=self.cache/(digest+'.json')
        if path.exists():
            try:
                stored=json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(stored,dict) or stored.get('request')!=identity:
                    raise ValueError('Cache request identity mismatch')
                self._validate_parsed(stage,stored.get('parsed'),validator)
            except (ValueError,TypeError,UnicodeError) as exc:
                failure_dir=self.cache/'failures';failure_dir.mkdir(exist_ok=True)
                failure_path=failure_dir/(digest+'-cached-'+uuid.uuid4().hex+'.json')
                path.replace(failure_path)
                self.events.append({'stage':stage,'cache_key':digest,'cache_hit':True,'status':'cache_rejected',
                    'reason':'business_validation' if isinstance(exc,BusinessValidationError) else 'invalid_cache',
                    'validation_reason':str(exc).replace(self.key,'[REDACTED]'),
                    'failure_file':str(failure_path),'usage_this_run':None})
                self.emit(self.events[-1])
            else:
                self.events.append({'stage':stage,'cache_key':digest,'cache_hit':True,'status':'cached','usage_this_run':None})
                self.emit(self.events[-1])
                return stored['parsed']
        if self.calls>=self.max_calls:raise RuntimeError('Request limit reached; no further API request sent')
        self.calls+=1
        event={'stage':stage,'cache_key':digest,'cache_hit':False,'status':'failed','usage_this_run':None,'attempt':attempt}
        self.events.append(event)
        try:
            response=self.transport(self.url,self.key,payload,self.timeout)
        except Exception as exc:
            event['error_type']=type(exc).__name__
            retryable=retryable_transport(exc)
            if isinstance(exc,HTTPError):
                event['http_status']=exc.code
            error=RetryableRequestError if retryable else RuntimeError
            self.emit(event)
            raise error('API request failed; check run events for attempt count and error type.') from None
        event['usage_this_run']=response.get('usage') if isinstance(response,dict) else None
        try:
            choice=response['choices'][0]
            event['finish_reason']=choice.get('finish_reason')
            if choice.get('finish_reason')!='stop':raise ValueError()
            if choice['message'].get('refusal'):
                event['finish_reason']='refusal'
                raise ValueError()
            content=choice['message']['content'].strip()
            if content.startswith('```') and content.endswith('```'):
                content=content.split('\n',1)[1].rsplit('```',1)[0]
            parsed=json.loads(content)
            self._validate_parsed(stage,parsed,validator)
        except (KeyError,IndexError,TypeError,ValueError,AttributeError) as exc:
            event['validation_error']='business_validation' if isinstance(exc,BusinessValidationError) else 'invalid_model_response'
            event['validation_reason']=str(exc).replace(self.key,'[REDACTED]')
            failure_dir=self.cache/'failures';failure_dir.mkdir(exist_ok=True)
            # No headers or credentials; retain rejected content separately from reusable cache.
            diagnostic={'request':identity,'response':response,'validation_error':event['validation_error'],
                        'validation_reason':event['validation_reason']}
            serialized=json.dumps(diagnostic,ensure_ascii=False,indent=2).replace(self.key,'[REDACTED]')
            failure_path=failure_dir/(digest+'-'+uuid.uuid4().hex+'.json')
            failure_path.write_text(serialized,encoding='utf-8')
            event['failure_file']=str(failure_path)
            self.emit(event)
            error=RetryableResponseError if event.get('finish_reason') in (None,'stop') else ValueError
            raise error('Invalid or truncated model JSON; rejected response saved separately') from None
        stored={'request':identity,'parsed':parsed,'usage':response.get('usage'),'response':response}
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(stored,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
        event['status']='ok'
        self.emit(event)
        return parsed
