"""Append real training evidence without rewriting historical observations."""
from copy import deepcopy
from recommendation.context import prepare_context
from .student_profile import StudentProfile, PROMPT_VERSION
from .run_store import atomic_json, fingerprint


def validate_update(base, records, catalog):
    prepare_context(catalog, student_id=base['student_id'],
                    allowed_concept_ids=list(catalog['concepts']), profile=base)
    if not records:
        raise ValueError('New evidence is required')
    builder = StudentProfile(base['student_id'], catalog['concepts'], lambda *a: None, model_id='validation')
    builder._validate_records(records)
    old_ids = {r['record_id'] for r in base['evidence']}
    sources = {(r['source']['file'], r['source']['index']) for r in base['evidence']}
    for r in records:
        source = r['source']
        if not isinstance(source.get('file'), str) or not source['file'] or type(source.get('index')) is not int:
            raise ValueError('Evidence requires source file and integer index')
        key = (source['file'], source['index'])
        if r['record_id'] in old_ids or key in sources:
            raise ValueError('Evidence already present')
        sources.add(key)


def run_update(store, client, base, records, catalog):
    validate_update(base, records, catalog)
    builder = StudentProfile(base['student_id'], catalog['concepts'], client.complete, model_id=client.model)
    added, failed = [], []
    summaries = deepcopy(base['summaries'])
    store.state['status'] = 'running'
    store.commit('update_started', base_sha256=fingerprint(base))
    store.plan_steps({'extract:'+r['record_id']: {'record_id': r['record_id']} for r in records})

    def step(key, metadata, action):
        saved = store.start_step(key, metadata)
        if saved is not None:
            return saved
        print('Step:', key, flush=True)
        try:
            result = action()
        except (ValueError, RuntimeError, OSError) as exc:
            store.fail_step(exc)
            failed.append(key)
            return None
        store.succeed_step(result)
        return result

    try:
        for row in records:
            result = step('extract:'+row['record_id'], {'record_id': row['record_id'],'model_id':builder.model_for('extract')}, lambda row=row: builder.insert([row]))
            if result is not None:
                added.extend(result)
        observations = deepcopy(base['observations']) + added
        affected = sorted({o['concept_id'] for o in added})
        if not failed:
            store.plan_steps({'summarize:'+c: {'concept_id': c} for c in affected})
            for c in affected:
                result = step('summarize:'+c, {'concept_id': c,'model_id':builder.model_for('summarize')},
                              lambda c=c: builder.generate_desc_for_every_kc([o for o in observations if o['concept_id']==c]))
                if result is not None:
                    summaries.update(result)
        status = 'partial' if failed else 'completed'
        revision = {'update_id': store.state['run_id'], 'base_sha256': fingerprint(base),
                    'stage_models':{'extract':builder.model_for('extract'),'summarize':builder.model_for('summarize')},
                    'records_sha256': fingerprint(records), 'base_model_id': base['model_id'],
                    'base_prompt_version': base['prompt_version'],
                    'added_evidence_ids': [r['record_id'] for r in records],
                    'affected_concept_ids': affected,
                    'changes': {c: {'before': base['summaries'].get(c), 'after': summaries.get(c)} for c in affected}}
        profile = None
        if not failed:
            profile = deepcopy(base)
            profile.update(status='completed', failed_steps=[],
                           model_id=client.model if builder.model_for('extract')==builder.model_for('summarize') else 'mixed',
                           stage_models={'extract':builder.model_for('extract'),'summarize':builder.model_for('summarize')},
                           prompt_version=PROMPT_VERSION, concepts=deepcopy(catalog['concepts']),
                           observations=observations, summaries=summaries,
                           evidence=deepcopy(base['evidence'])+deepcopy(records),
                           revisions=deepcopy(base.get('revisions', []))+[revision])
            prepare_context(catalog, student_id=base['student_id'], allowed_concept_ids=list(catalog['concepts']), profile=profile)
            atomic_json(store.path/'profile.json', profile)
        report = {'status': status, 'failed_steps': failed, 'revision': revision,
                  'added_observations': added, 'profile': profile}
        atomic_json(store.path/'update.json', report)
        store.state['status'] = status
        store.commit('update_finished', status=status)
        return report
    except BaseException as exc:
        if store.active_step and store.state['steps'][store.active_step]['status']=='running':
            store.state['steps'][store.active_step]['status']='interrupted'
        store.state['status']='interrupted'
        store.commit('update_interrupted', error=store.error(exc))
        raise
    finally:
        store.export()
