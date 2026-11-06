"""Business checkpoints: validated extraction records and concept summaries."""
from .student_profile import StudentProfile, PROMPT_VERSION
from .run_store import atomic_json


def run_profile(store,client,records,concepts,model):
    builder=StudentProfile(records[0]['student_id'],concepts,client.complete,model_id=model)
    builder._validate_records(records)
    observations=[];summaries={};failed=[]
    store.state['status']='running';store.commit('workflow_started')
    store.plan_steps({'extract:'+r['record_id']:{'record_id':r['record_id'],'problem_id':r['problem_id']} for r in records})

    def step(key,metadata,action):
        saved=store.start_step(key,metadata)
        if saved is not None:return saved
        print('Step:',key,flush=True)
        try:
            result=action()
        except (ValueError,RuntimeError,OSError) as exc:
            store.fail_step(exc);failed.append(key)
            print('Failed:',key,'(see trace)',flush=True)
            return None
        store.succeed_step(result)
        return result

    try:
        for r in records:
            result=step('extract:'+r['record_id'],{'record_id':r['record_id'],'problem_id':r['problem_id'],'model_id':builder.model_for('extract')},
                        lambda r=r:builder.insert([r]))
            if result is not None:observations.extend(result)
        if not failed:
            store.plan_steps({'summarize:'+c:{'concept_id':c} for c in sorted({o['concept_id'] for o in observations})})
            for concept in sorted({o['concept_id'] for o in observations}):
                result=step('summarize:'+concept,{'concept_id':concept,'model_id':builder.model_for('summarize')},
                    lambda concept=concept:builder.generate_desc_for_every_kc([o for o in observations if o['concept_id']==concept]))
                if result is not None:summaries.update(result)
        else:store.commit('summaries_blocked',reason='Extraction incomplete; do not summarize partial source evidence')
        status='partial' if failed else 'completed'
        profile={'status':status,'failed_steps':failed,'student_id':builder.student_id,
                 'model_id':model if builder.model_for('extract')==builder.model_for('summarize') else 'mixed',
                 'stage_models':{'extract':builder.model_for('extract'),'summarize':builder.model_for('summarize')},
                 'prompt_version':PROMPT_VERSION,'concepts':concepts,'observations':observations,
                 'summaries':summaries,'evidence':records}
        atomic_json(store.path/('profile.json' if status=='completed' else 'partial-profile.json'),profile)
        store.state['status']=status;store.commit('workflow_finished',status=status)
        return profile
    except BaseException as exc:
        if store.active_step and store.state['steps'][store.active_step]['status']=='running':
            store.state['steps'][store.active_step]['status']='interrupted'
        store.state['status']='interrupted';store.commit('workflow_interrupted',error=store.error(exc))
        raise
    finally:store.export()
