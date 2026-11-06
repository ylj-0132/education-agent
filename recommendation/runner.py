"""Formal direct recommendation. No candidate generation or implicit API calls."""
from copy import deepcopy
import time

from profiling.run_store import atomic_json, fingerprint, stamp
from .comparison import PROMPT_VERSION, make_model_input, validate_decision, _metrics, relation_handoff, review_relations
from .context import build_generation_request


def run_recommendation(store, client, context):
    """Run one durable B decision; only a validated recommendation gets a handoff.

    The caller supplies a RunStore with input/config/code identity and a client
    connected to RecordedTransport/store.api_event. recommendation.json is the
    authoritative round output; generation-request.json is only a convenience.
    """
    key = 'B.direct'
    ctx_hash = fingerprint(context)
    round_info = store.state.setdefault('recommendation_round', {
        'recommendation_id': store.state['run_id'] + ':recommendation',
        'created_at': stamp(), 'context_sha256': ctx_hash})
    if round_info['context_sha256'] != ctx_hash:
        raise ValueError('Recommendation context mismatch; cannot resume a different round')
    store.state['status'] = 'running'
    store.commit('recommendation_started', context_sha256=ctx_hash)
    store.plan_steps({key: {'stage': 'recommend_direct'}})
    decision, error, request = None, None, None
    try:
        atomic_json(store.path/'context.json', context)
        prompt = make_model_input(context, 'direct')
        previous_elapsed = store.state['steps'][key].get('elapsed_seconds', 0)
        saved = store.start_step(key, {'stage': 'recommend_direct',
            'logical_text_sha256': fingerprint(prompt)})
        started = time.monotonic()
        try:
            if saved is not None:
                validate_decision(context, saved)
                decision = saved
                store.commit('checkpoint_reused')
            else:
                decision = client.complete_validated('recommend_direct', prompt,
                    lambda v: validate_decision(context, v))
                store.state['steps'][key]['elapsed_seconds'] = previous_elapsed + time.monotonic() - started
                store.succeed_step(decision)
            if decision['decision'] == 'recommend':
                request = build_generation_request(context,
                    selected_concept_ids=decision['concept_ids'], reason=decision['reason'],
                    evidence_ids=decision['evidence_ids'], selection_origin='model_direct')
                request.update(recommendation_id=round_info['recommendation_id'], run_id=store.state['run_id'],
                    recommendation_context_sha256=ctx_hash, decision_kind='model_inference',
                    recommendation_uncertainties=deepcopy(decision['uncertainties']),
                    recommendation_relation_ids=deepcopy(decision['relation_ids']))
                request.update(relation_handoff(context, decision))
        except (ValueError, RuntimeError, OSError) as exc:
            store.state['steps'][key]['elapsed_seconds'] = previous_elapsed + time.monotonic() - started
            store.fail_step(exc)
            decision, request, error = None, None, store.error(exc)
        status = ('recommended' if decision['decision'] == 'recommend' else 'insufficient_evidence') if decision else 'failed'
        report = {
            'schema': 'recommendation-round-v1', **round_info, 'run_id': store.state['run_id'],
            'status': status, 'execution_status': 'failed' if error else 'completed',
            'student_id': context['student_id'], 'catalog_version': context['catalog_version'],
            'profile_source': deepcopy(context['profile_source']), 'identity': deepcopy(store.state['identity']),
            'prompt_version': PROMPT_VERSION, 'message_layout': 'single_user', 'strategy': 'B',
            'allowed_concept_ids': deepcopy(context['allowed_concept_ids']),
            'goal_concept_ids': deepcopy(context['goal_concept_ids']), 'requirements': deepcopy(context['requirements']),
            'decision_kind': 'model_inference', 'decision': decision, 'error': error,
            'relation_review': review_relations(context, decision) if decision else None,
            'generation_request': request, 'metrics': _metrics(store.snapshot(), 'B'),
            'review_required': ['核对理由是否被证据支持以及先修条件是否适用；结构校验不保证内容准确。'],
            'limitations': ['推荐不更新学生掌握状态。', '出题交接对象已生成不代表题目已经生成或检查。']}
        handoff = store.path/'generation-request.json'
        if request is not None:
            atomic_json(handoff, request)
        elif handoff.exists():
            handoff.unlink()
        atomic_json(store.path/'recommendation.json', report)
        store.state['status'] = report['execution_status']
        store.commit('recommendation_finished', outcome=status)
        return report
    except BaseException as exc:
        if store.active_step and store.state['steps'][store.active_step]['status'] == 'running':
            store.state['steps'][store.active_step]['status'] = 'interrupted'
        store.state['status'] = 'interrupted'
        store.commit('recommendation_interrupted', error=store.error(exc))
        raise
    finally:
        store.export()
