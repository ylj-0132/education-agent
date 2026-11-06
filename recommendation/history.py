"""Read-only, student-isolated join of explicitly selected durable task records."""
from copy import deepcopy
import json
from pathlib import Path
from profiling.run_store import RunStore, fingerprint
from profiling.java_execution import attach_verifications, execution_model_input


def load_usage_history(manifest_path, student_id):
    # Lazy import avoids the generation -> context -> history import cycle.
    from submissions.workflow import load_assignment, load_submission_evidence
    path=Path(manifest_path).resolve()
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    fields={'schema','student_id','generation_runs','submission_runs','verification_runs'}
    if (not isinstance(data,dict) or set(data)!=fields
            or data['schema']!='question-usage-sources-v1' or data['student_id']!=student_id):
        raise ValueError('Usage sources must belong to the selected student')
    def paths(key):
        values=data[key]
        if not isinstance(values,list) or any(not isinstance(v,str) or not v.strip() for v in values):
            raise ValueError('Usage sources must be path lists')
        return list(dict.fromkeys((path.parent/Path(v)).resolve() for v in values))
    questions={}
    for run in paths('generation_runs'):
        a=load_assignment(run)
        if a['student_id']!=student_id:raise ValueError('Usage generation belongs to another student')
        qid=a['question_id']
        req=json.loads((run/'request.json').read_text(encoding='utf-8'))
        item={k:deepcopy(a[k]) for k in ('student_id','question_id','question_sha256','question_version',
            'generation_run_id','recommendation_id','generation_status','ready_for_student')}
        item.update(target_concept_ids=deepcopy(req['target_concept_ids']),
            question={k:deepcopy(a['question'][k]) for k in ('title','statement','input_format','output_format','constraints','samples')},
            status='generated_no_received_answer',delivery_status='not_recorded',attempts=[],
            source={'generation_run':str(run),'request_sha256':fingerprint(req)})
        if qid in questions and questions[qid]!=item:raise ValueError('Conflicting question versions')
        questions[qid]=item
    rows={};sources={}
    for run in paths('submission_runs'):
        row=load_submission_evidence(run)
        if row['student_id']!=student_id:raise ValueError('Usage submission belongs to another student')
        meta=row['submission'];q=questions.get(meta['question_id'])
        if q is None or any(meta[k]!=q[k] for k in ('question_id','question_sha256','question_version','generation_run_id','recommendation_id')):
            raise ValueError('Usage submission does not match a selected generated question')
        # Reconstitute the public question binding, not merely the question ID.
        if row['question']!=json.dumps(q['question'],ensure_ascii=False):
            raise ValueError('Usage submission question text changed')
        rid=row['record_id']
        if rid in rows and rows[rid]!=row:raise ValueError('Conflicting submission identity')
        rows[rid]=row;sources.setdefault(rid,[]).append(str(run))
    verified=attach_verifications(list(rows.values()),paths('verification_runs'))
    for row in verified:
        q=questions[row['problem_id']];meta=row['submission']
        q['status']='answer_received'
        q['attempts'].append({'evidence_id':row['record_id'],
            'submission_id':meta['submission_id'],'submitted_at':meta['submitted_at'],
            'received_at':meta['received_at'],'program_sha256':meta['program_sha256'],
            'execution_observation':execution_model_input(row),
            'source':{'submission_runs':sources[row['record_id']],
                      'execution_verification':deepcopy(row.get('execution_verification'))}})
    for q in questions.values():q['attempts'].sort(key=lambda a:(a['received_at'],a['evidence_id']))
    return [questions[k] for k in sorted(questions)]


def usage_context(history, student_id, allowed, evidence, groups):
    """Separate usage observations from knowledge state; never create mastery labels."""
    if not isinstance(history,list):raise ValueError('Usage history must be a list')
    result=[];seen=set()
    for item in history:
        if not isinstance(item,dict) or item.get('student_id')!=student_id:
            raise ValueError('Usage history belongs to another student')
        qid=item.get('question_id')
        if not isinstance(qid,str) or qid in seen:raise ValueError('Duplicate or missing usage question')
        seen.add(qid)
        if not set(item['target_concept_ids']) & set(allowed):continue
        q=deepcopy(item)
        for a in q['attempts']:
            row=evidence.get(a['evidence_id'])
            if row is not None:
                if (row.get('submission',{}).get('question_sha256')!=q['question_sha256']
                        or fingerprint(row['program'])!=a['program_sha256']):
                    raise ValueError('Profile and usage submission disagree')
                existing=row.get('execution_observation')
                if a['execution_observation'] is not None and existing is not None and a['execution_observation']!=existing:
                    raise ValueError('Choose a consistent execution run for profile and usage')
                if a['execution_observation'] is None:a['execution_observation']=deepcopy(existing)
            a['profile_evidence_status']='included' if row else 'not_in_supplied_profile'
            a['model_observations']=[deepcopy(o) for k,items in groups.items() if k in allowed
                                     for o in items if o['evidence_id']==a['evidence_id']]
        result.append(q)
    return result
