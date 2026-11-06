"""Evidence and relation interfaces, without KT, ranking or implicit selection."""
from collections import Counter, defaultdict
from copy import deepcopy
from profiling.evidence import validate_learning_evidence
from profiling.java_execution import execution_model_input
from .history import usage_context


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _ids(values, name, *, allowed=None, nonempty=False):
    if not isinstance(values, (list, tuple)) or any(not _text(v) for v in values):
        raise ValueError(f'{name} must be a list of nonempty strings')
    if len(set(values)) != len(values) or (nonempty and not values):
        raise ValueError(f'{name} must be unique and satisfy the required scope')
    if allowed is not None and not set(values) <= set(allowed):
        raise ValueError(f'{name} contains IDs outside the allowed scope')
    return list(values)


def _catalog(catalog):
    if not isinstance(catalog, dict) or not _text(catalog.get('version')):
        raise ValueError('Catalog version is required')
    concepts = catalog.get('concepts')
    if not isinstance(concepts, dict) or not concepts or any(
            not _text(k) or not _text(v) for k, v in concepts.items()):
        raise ValueError('Catalog must map concept IDs to definitions')
    sources = catalog.get('relation_sources', {})
    if not isinstance(sources, dict):
        raise ValueError('Invalid relation sources')
    seen_ids, seen_edges = set(), set()
    graph = {k: [] for k in concepts}
    for kind in ('prerequisites', 'associations'):
        edges = catalog.get(kind, [])
        if not isinstance(edges, list):
            raise ValueError('Relations must be lists')
        for e in edges:
            if not isinstance(e, dict) or not _text(e.get('id')) or e['id'] in seen_ids:
                raise ValueError('Relation IDs must be unique')
            seen_ids.add(e['id'])
            _ids(e.get('source_ids'), 'relation sources', allowed=sources, nonempty=True)
            if not _text(e.get('status')):
                raise ValueError('Relation review status is required')
            if kind == 'prerequisites':
                pair = _ids([e.get('from'), e.get('to')], 'prerequisite endpoints', allowed=concepts)
                if not _text(e.get('applies_when')) or not _text(e.get('reason')):
                    raise ValueError('Prerequisite scope and reason are required')
                graph[pair[0]].append(pair[1])
            else:
                pair = _ids(e.get('concept_ids'), 'association endpoints', allowed=concepts)
                if len(pair) != 2 or not _text(e.get('use_case')):
                    raise ValueError('Association needs two endpoints and a use case')
                pair = sorted(pair)
            key = (kind, *pair)
            if key in seen_edges:
                raise ValueError('Duplicate relation')
            seen_edges.add(key)
    active, done = set(), set()

    def visit(k):
        if k in active:
            raise ValueError('Prerequisite cycle')
        if k in done:
            return
        active.add(k)
        for other in graph[k]:
            visit(other)
        active.remove(k)
        done.add(k)

    for k in graph:
        visit(k)
    return concepts


def _profile(profile, student_id, concepts):
    """Validate all supplied records before exposing any personal context."""
    if profile is None:
        return {}, {}, {}, None
    if not isinstance(profile, dict) or profile.get('student_id') != student_id:
        raise ValueError('Profile belongs to another student')
    if profile.get('status') not in (None, 'completed') or profile.get('failed_steps'):
        raise ValueError('Partial or interrupted profile cannot be used as complete input')
    original_concepts = profile.get('concepts')
    if not isinstance(original_concepts, dict) or any(
            k not in concepts or concepts[k] != v for k, v in original_concepts.items()):
        raise ValueError('Profile concept definitions require explicit migration')
    rows, observations, summaries = (profile.get(k) for k in ('evidence', 'observations', 'summaries'))
    if not isinstance(rows, list) or not isinstance(observations, list) or not isinstance(summaries, dict):
        raise ValueError('Missing structured profile evidence')
    evidence, groups, seen = {}, defaultdict(list), set()
    for row in rows:
        validate_learning_evidence(row, student_id)
        if any(not _text(row.get(k)) for k in ('record_id', 'problem_id', 'question', 'program')):
            raise ValueError('Missing original question, submission or evidence ID')
        if row['record_id'] in evidence or not isinstance(row.get('source'), dict) or not row['source']:
            raise ValueError('Duplicate evidence or missing provenance')
        source = row['source']
        if (not _text(source.get('file')) or type(source.get('index')) is not int
                or source['index'] < 0):
            raise ValueError('Provenance requires a source file and nonnegative index')
        evidence[row['record_id']] = {
            k: deepcopy(row[k]) for k in ('record_id', 'student_id', 'split', 'problem_id',
                                         'question', 'program')}
        evidence[row['record_id']]['source'] = {
            k: deepcopy(source[k]) for k in ('file', 'index', 'sha256') if k in source}
        execution = execution_model_input(row)
        if execution is not None:
            evidence[row['record_id']]['execution_observation'] = execution
        if row['split'] == 'live':
            evidence[row['record_id']]['evidence_origin'] = row['evidence_origin']
            evidence[row['record_id']]['submission'] = {
                k: deepcopy(row['submission'][k]) for k in ('submission_id','question_id','question_sha256',
                    'question_version','generation_run_id','received_at','submitted_at','program_sha256','question_text_sha256')}
    for o in observations:
        if not isinstance(o, dict) or any(not _text(o.get(k)) for k in
                                        ('concept_id', 'evidence_id', 'description')):
            raise ValueError('Invalid observation')
        if (o['concept_id'] not in original_concepts or o['evidence_id'] not in evidence
                or o.get('student_id') != student_id or o.get('kind') != 'model_inference'
                or o.get('status') not in ('Good', 'Bad', 'Unknown')):
            raise ValueError('Invalid observation provenance or status')
        pair = (o['concept_id'], o['evidence_id'])
        if pair in seen:
            raise ValueError('Duplicate observation for concept and evidence')
        seen.add(pair)
        groups[o['concept_id']].append({k: deepcopy(o[k]) for k in
            ('concept_id', 'evidence_id', 'description', 'student_id', 'kind', 'status')})
        if 'model_id' in o:groups[o['concept_id']][-1]['model_id']=o['model_id']
    for k, summary in summaries.items():
        if (k not in groups or not isinstance(summary, dict)
                or not _text(summary.get('evaluation')) or summary.get('kind') != 'model_inference'):
            raise ValueError('Summary needs linked model observations')
        refs = _ids(summary.get('evidence_ids'), 'summary evidence', allowed=evidence, nonempty=True)
        expected = {o['evidence_id'] for o in groups[k]}
        counts = summary.get('counts')
        if (set(refs) != expected or not isinstance(counts, dict)
                or any(type(v) is not int or v <= 0 for v in counts.values())
                or counts != dict(Counter(o['status'] for o in groups[k]))):
            raise ValueError('Summary references or counts disagree with observations')
    safe_summaries = {k: {field: deepcopy(v[field]) for field in
                            ('evaluation', 'kind', 'counts', 'evidence_ids')}
                      for k, v in summaries.items()}
    source = {k: deepcopy(profile.get(k)) for k in ('model_id', 'prompt_version', 'status')}
    if 'stage_models' in profile:source['stage_models']=deepcopy(profile['stage_models'])
    for k,v in summaries.items():
        if 'model_id' in v:safe_summaries[k]['model_id']=v['model_id']
    source['observed_concept_ids'] = sorted(groups)
    return evidence, groups, safe_summaries, source


def _relations(catalog, wanted, allowed):
    prerequisites = []
    for e in catalog.get('prerequisites', []):
        if e['to'] in wanted:
            prerequisites.append(deepcopy(e) | {
                'foundation_in_scope': e['from'] in allowed,
                'applicability': 'not_assessed', 'enforced': False})
    associations = [deepcopy(e) for e in catalog.get('associations', [])
                    if set(e['concept_ids']) <= set(wanted)]
    source_ids = {k for e in prerequisites + associations for k in e['source_ids']}
    return {'prerequisites': prerequisites, 'associations': associations,
            'sources': {k: deepcopy(catalog['relation_sources'][k]) for k in sorted(source_ids)}}


def prepare_context(catalog, *, student_id, allowed_concept_ids, goal_concept_ids=(),
                    requirements=(), profile=None, usage_history=None):
    """Prepare data for a future selector; never score, rank, or choose a bundle."""
    concepts = _catalog(catalog)
    if not _text(student_id):
        raise ValueError('Student ID is required for an isolated context')
    allowed = _ids(allowed_concept_ids, 'teacher scope', allowed=concepts, nonempty=True)
    goals = _ids(goal_concept_ids, 'learning goals', allowed=allowed)
    if not isinstance(requirements, (list, tuple)) or any(not _text(v) for v in requirements):
        raise ValueError('Requirements must be a list of nonempty strings')
    evidence, groups, summaries, source = _profile(profile, student_id, concepts)
    states = {}
    for k in allowed:
        items = groups.get(k, [])
        states[k] = {'definition': concepts[k],
                     'evidence_status': 'model_inferences_available' if items else 'no_observation',
                     'counts': dict(Counter(o['status'] for o in items)),
                     'observations': deepcopy(items), 'summary': deepcopy(summaries.get(k))}
    ids = {o['evidence_id'] for v in states.values() for o in v['observations']}
    context = {
        'schema': 'recommendation-context-v1', 'status': 'awaiting_policy',
        'student_id': student_id, 'catalog_version': catalog['version'],
        'allowed_concept_ids': allowed, 'goal_concept_ids': goals,
        'requirements': list(requirements), 'profile_source': source,
        'concept_context': states, 'evidence': [deepcopy(evidence[k]) for k in sorted(ids)],
        'relations': _relations(catalog, set(allowed), set(allowed)),
        'selection_policy': 'model_direct',
        'limitations': ['画像与摘要为模型推断，可能误归因，不是独立判题或掌握概率。',
                        '没有观察不表示不会；关系不自动传播状态，draft先修不强制拦截。',
                        '仅准备接口上下文，未自动推荐；没有使用时间信息估计遗忘。'],
    }
    if usage_history is not None:
        context['question_usage']=usage_context(usage_history,student_id,allowed,evidence,groups)
        context['limitations'].append('使用记录仅覆盖显式清单；生成不表示已交付，无收到作答不证明学生未做；运行结果不自动更新画像。')
    return context


def build_generation_request(context, *, selected_concept_ids, reason, evidence_ids=(),
                             selection_origin='explicit_selection'):
    """Package supplied targets with their actual selection provenance."""
    if not isinstance(context, dict) or context.get('schema') != 'recommendation-context-v1':
        raise ValueError('Expected a prepared recommendation context')
    selected = _ids(selected_concept_ids, 'selected targets',
                    allowed=context['allowed_concept_ids'], nonempty=True)
    if not _text(reason):
        raise ValueError('Selection reason is required')
    if selection_origin not in ('explicit_selection', 'model_two_stage', 'model_direct'):
        raise ValueError('Unknown selection origin')
    states = {k: deepcopy(context['concept_context'][k]) for k in selected}
    relevant_ids = {o['evidence_id'] for v in states.values() for o in v['observations']}
    cited = _ids(evidence_ids, 'reason evidence', allowed=relevant_ids)
    evidence = [deepcopy(r) for r in context['evidence'] if r['record_id'] in relevant_ids]
    relations = deepcopy(context['relations'])
    relations['prerequisites'] = [e for e in relations['prerequisites'] if e['to'] in selected]
    relations['associations'] = [e for e in relations['associations'] if set(e['concept_ids']) <= set(selected)]
    source_ids = {k for e in relations['prerequisites'] + relations['associations'] for k in e['source_ids']}
    relations['sources'] = {k: v for k, v in relations['sources'].items() if k in source_ids}
    request = {
        'schema': 'generation-request-v1', 'status': 'targets_supplied',
        'target_source': selection_origin, 'student_id': context['student_id'],
        'catalog_version': context['catalog_version'], 'target_concept_ids': selected,
        'allowed_concept_ids': deepcopy(context['allowed_concept_ids']),
        'goal_concept_ids': deepcopy(context['goal_concept_ids']),
        'requirements': deepcopy(context['requirements']), 'selection_reason': reason,
        'reason_evidence_ids': cited, 'profile_source': deepcopy(context['profile_source']),
        'concept_context': states, 'evidence': evidence, 'relations': relations,
        'previous_submission_problem_ids': sorted({r['problem_id'] for r in evidence}),
        'limitations': ([v for v in context['limitations'] if '未自动推荐' not in v]
                        + ['本次目标选择为模型推断，尚未验证教学效果。']
                        if selection_origin != 'explicit_selection' else
                        deepcopy(context['limitations']) + ['目标由调用方明确指定，未经过自动组合选择。']),
    }
    if 'question_usage' in context:
        request['question_usage']=[deepcopy(q) for q in context['question_usage']
                                   if set(q['target_concept_ids']) & set(selected)]
        for q in request['question_usage']:
            for a in q['attempts']:
                a['model_observations']=[o for o in a['model_observations'] if o['concept_id'] in selected]
        request['previous_submission_problem_ids']=sorted(set(request['previous_submission_problem_ids']) |
            {q['question_id'] for q in request['question_usage'] if q['attempts']})
    return request
