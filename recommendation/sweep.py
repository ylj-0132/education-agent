"""Small paired case/layout experiment, with per-cell durable request budgets."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from profiling.api_client import CachedChatClient, refusal_summary, send_chat
from profiling.run_store import RunStore, RecordedTransport, atomic_json, fingerprint
from .context import prepare_context
from .comparison import PROMPT_VERSION, run_comparison

LAYOUTS = ('single_user', 'split_roles')


def build_cases(catalog, profile):
    """One existing single-question profile and two genuine no-history inputs."""
    definitions = [
        ('restricted_scope', 'existing_single_question_profile', profile,
         ['java.syntax'], ['java.syntax'],
         ['教师本轮仅允许复查Java数字字面量，不扩展到输入、数组或循环；保留诊断不确定性。'],
         '不得越过教师范围；一次Bad不能升级为稳定知识缺陷。'),
        ('cold_start', 'cold_start_no_history', None,
         ['java.input', 'java.array', 'java.loop', 'algorithm.counting'], ['java.array', 'java.loop'],
         ['这是首次练习，没有历史作答；安排基础诊断，不能声称学生已有错误或已掌握。'],
         '未知状态不作薄弱结论；可提出合理诊断或说明信息不足。'),
        ('conditional_prerequisite', 'cold_start_no_history', None,
         ['algorithm.dynamic_programming', 'java.loop', 'java.integer_arithmetic'], ['algorithm.dynamic_programming'],
         ['尚未指定动态规划的实现方式；考虑所需前置诊断，但不得扩大教师允许范围，不假定已掌握基础。'],
         '数组仅为数组表实现的条件先修；不能推荐范围外数组，也不能把草稿边当成无条件禁令。'),
    ]
    return [{'case_id': name, 'origin': origin, 'review_focus': focus,
             'context': prepare_context(catalog, student_id=profile['student_id'], profile=p,
                allowed_concept_ids=scope, goal_concept_ids=goals, requirements=requirements)}
            for name, origin, p, scope, goals, requirements, focus in definitions]


def _outcome(attempt):
    if attempt['status'] == 'accepted':
        return 'accepted'
    if attempt['status'] in ('failed', 'unknown', 'sent'):
        return 'transport_or_unknown'
    response = attempt.get('response', {})
    message = response.get('choices', [{}])[0].get('message', {})
    if message.get('refusal') or refusal_summary({'evaluation': message.get('content', '')}):
        return 'service_refusal'
    return 'other_invalid_response'


def summarize(output, cases):
    cells, role_pairs, primary, all_outcomes = [], defaultdict(dict), defaultdict(Counter), defaultdict(Counter)
    usage, requests, endpoint_blocked = Counter(), 0, False
    for case in cases:
        for layout in LAYOUTS:
            path = output/'tasks'/case['case_id']/layout
            if not (path/'checkpoint.sqlite3').exists():
                cells.append({'case_id': case['case_id'], 'message_layout': layout, 'status': 'not_run'})
                continue
            snap = RunStore.inspect(path, full=True)
            report_path = path/'comparison.json'
            report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else None
            cells.append({'case_id': case['case_id'], 'origin': case['origin'], 'message_layout': layout,
                          'status': snap['status'], 'review_focus': case['review_focus'],
                          'report_file': str(report_path), 'arms': report['arms'] if report else None})
            endpoint_blocked |= bool(snap.get('blocked_reason'))
            requests += len(snap['attempts'])
            for a in snap['attempts']:
                all_outcomes[layout][_outcome(a)] += 1
                usage.update({k: v for k, v in (a.get('usage') or {}).items()
                              if k in ('prompt_tokens', 'completion_tokens', 'total_tokens') and type(v) is int})
                if a['step_id'] in ('A.candidates', 'B.direct') and a['number'] == 1:
                    primary[layout][_outcome(a)] += 1
            for step in ('A.candidates', 'B.direct'):
                value = snap['steps'].get(step, {}).get('metadata', {}).get('logical_text_sha256')
                if value:
                    role_pairs[(case['case_id'], step)][layout] = value
    matched = sum(len(v) == 2 and v[LAYOUTS[0]] == v[LAYOUTS[1]] for v in role_pairs.values())
    comparable = sum(len(v) == 2 for v in role_pairs.values())
    if matched != comparable:
        raise ValueError('Paired primary-stage text differs; cannot compare message layouts')
    status = ('stopped_endpoint_error' if endpoint_blocked else
              'completed' if all(c['status'] == 'completed' for c in cells) else 'partial')
    return {'schema': 'recommendation-sweep-v1', 'status': status, 'requests': requests,
            'reported_usage': dict(usage), 'cost_cny': None, 'cells': cells,
            'role_probe': {'matched_logical_text_pairs': matched, 'comparable_pairs': comparable,
                           'first_attempt_outcomes': {k: dict(primary[k]) for k in LAYOUTS},
                           'all_attempt_outcomes_exploratory': {k: dict(all_outcomes[k]) for k in LAYOUTS},
                           'excluded_mediated_stage': 'A.select'},
            'limitations': ['仅3种情形，缺少统计规模，拒答差异不能直接证明服务端内部根因。',
                            'A.select候选由上游生成，不保证布局间输入相同，不用于纯角色因果比较。',
                            'v2调整了规则和数据的排列；两种新布局的文本一致，但不能与旧v1重试频率直接合并。',
                            '未更换服务商或模型，不启用JSON模式；未验证真实教学效果。']}


def run_sweep(cases, settings, output, *, resume=False, transport=send_chat, source_inputs=None):
    output = Path(output)
    if len(cases) != 3 or len({c['case_id'] for c in cases}) != 3:
        raise ValueError('Expected three distinct predefined cases')
    base_url = settings.get('ZZZ_BASE_URL', '')
    parts = urlsplit(base_url)
    if (parts.scheme != 'https' or not parts.netloc or parts.username or parts.query or parts.fragment
            or not settings.get('ZZZ_API_KEY', '').strip() or not settings.get('ZZZ_MODEL', '').strip()):
        raise ValueError('Valid HTTPS endpoint, key and model are required')
    limit = min(6, int(settings.get('ZZZ_MAX_CALLS', '12')))
    max_tokens = int(settings.get('ZZZ_MAX_TOKENS', '1500'))
    timeout = int(settings.get('ZZZ_TIMEOUT', '60'))
    if min(limit, max_tokens, timeout) < 1:
        raise ValueError('Request limits must be positive')
    base = Path(__file__).resolve().parent.parent
    code = [*sorted((base/'recommendation').glob('*.py')), *sorted((base/'profiling').glob('*.py')),
            base/'sweep_recommendation.py']
    identity = {'cases_sha256': fingerprint(cases), 'source_inputs': source_inputs or {},
                'model': settings['ZZZ_MODEL'], 'base_url': base_url, 'max_tokens': max_tokens,
                'timeout': timeout, 'per_cell_request_limit': limit, 'suite_request_limit': 6*limit,
                'prompt_version': PROMPT_VERSION, 'maximum_concurrency': 2,
                'implementation_sha256': {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code}}
    if resume:
        old = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
        if old['identity'] != identity:
            raise ValueError('Suite input/config/version mismatch')
    else:
        output.mkdir(parents=True, exist_ok=False)
        atomic_json(output/'manifest.json', {'identity': identity, 'cases': cases,
                    'normal_logical_calls': 18, 'maximum_calls_including_retries': 6*limit})

    def cell(case, layout, order):
        path = output/'tasks'/case['case_id']/layout
        client = CachedChatClient(base_url=base_url, api_key=settings['ZZZ_API_KEY'], model=settings['ZZZ_MODEL'],
                                  cache_dir=output/'cache'/case['case_id']/layout,
                                  max_calls=limit, max_tokens=max_tokens, timeout=timeout)
        task_identity = identity | {'case_id': case['case_id'], 'layout': layout, 'order': order}
        with RunStore(path, task_identity, limit, resume=(path/'checkpoint.sqlite3').exists(), secret=client.key) as store:
            client.transport = RecordedTransport(store, transport); client.event_sink = store.api_event
            print('Cell:', case['case_id'], layout, order, flush=True)
            return run_comparison(store, client, case['context'], order=order, message_layout=layout)

    for i, case in enumerate(cases):
        # Pair layouts in the same time window, max two connections. Alternate arm order by case.
        order = 'B-first' if i % 2 == 0 else 'A-first'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(cell, case, layout, order) for layout in LAYOUTS]
            for future in futures:
                future.result()
        report = summarize(output, cases)
        atomic_json(output/'suite.json', report)
        print('Case finished:', case['case_id'], 'cumulative requests:', report['requests'], flush=True)
        if report['status'] == 'stopped_endpoint_error':
            break
    report = summarize(output, cases)
    atomic_json(output/'suite.json', report)
    return report
