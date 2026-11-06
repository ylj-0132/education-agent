"""Preview Java generation; --execute explicitly enables bounded paid calls."""
import argparse
import hashlib
import json
from pathlib import Path

from profiling.api_client import CachedChatClient, read_env, send_chat, model_config
from profiling.run_store import RunStore, RecordedTransport, atomic_json, fingerprint
from recommendation.history import load_usage_history
from recommendation.context import prepare_context, build_generation_request
from generation.workflow import PROMPT_VERSION, load_recommendation, run_generation, validate_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recommendation-run', type=Path)
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--usage-history', type=Path, help='Explicit question-usage-sources-v1 manifest')
    parser.add_argument('--student')
    parser.add_argument('--scope', nargs='+')
    parser.add_argument('--targets', nargs='+')
    parser.add_argument('--requirement', action='append', default=[])
    parser.add_argument('--output', type=Path, default=Path('runs/generation-001'))
    parser.add_argument('--cache', type=Path, default=Path('cache/generation'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--max-calls', type=int, choices=range(1, 9), default=8)
    parser.add_argument('--max-output-tokens', type=int, default=6000,
                        help='Generation/review output budget including reasoning; independent of profiling ZZZ_MAX_TOKENS')
    for option in ('execute', 'resume', 'status', 'trace'):
        parser.add_argument('--'+option, action='store_true')
    args = parser.parse_args()
    if args.max_output_tokens < 1: parser.error('--max-output-tokens must be positive')
    if args.status or args.trace:
        if args.execute or args.resume: parser.error('Inspection cannot be combined with execution')
        print(json.dumps(RunStore.inspect(args.output, full=args.trace), ensure_ascii=False, indent=2)); return
    if args.resume and not args.execute: parser.error('--resume requires --execute')
    inputs = {}
    def read(path, name):
        data = path.read_bytes(); inputs[name] = {'file': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        return json.loads(data.decode('utf-8-sig'))
    if args.recommendation_run:
        if args.student or args.scope or args.targets or args.profile or args.requirement or args.usage_history:
            parser.error('Use either an unchanged recommendation run or explicit teacher inputs')
        req = load_recommendation(args.recommendation_run)
        read(args.recommendation_run/'recommendation.json', 'recommendation')
        read(args.recommendation_run/'context.json', 'context')
    else:
        if not args.student or not args.scope or not args.targets or not args.requirement:
            parser.error('Teacher entry requires --student --scope --targets and --requirement')
        catalog = read(args.catalog, 'catalog')
        profile = read(args.profile, 'profile') if args.profile else None
        history = load_usage_history(args.usage_history,args.student) if args.usage_history else None
        if args.usage_history: read(args.usage_history,'usage_history')
        ctx = prepare_context(catalog, student_id=args.student, allowed_concept_ids=args.scope,
            goal_concept_ids=args.targets, requirements=args.requirement, profile=profile, usage_history=history)
        req = build_generation_request(ctx, selected_concept_ids=args.targets, reason='教师直接指定目标与要求')
    validate_request(req)
    print(json.dumps({'mode': 'execute' if args.execute else 'preview', 'request_sha256': fingerprint(req),
        'usage_history_supplied':'question_usage' in req,'question_usage_records':len(req.get('question_usage',[])),
        'target_source': req['target_source'], 'target_concept_ids': req['target_concept_ids'],
        'max_content_revisions': 1, 'max_logical_calls': 4, 'request_limit_including_retries': args.max_calls,
        'max_output_tokens': args.max_output_tokens,
        'java_execution': 'separate_explicit_approval', 'ready_for_student': False}, ensure_ascii=False, indent=2), flush=True)
    if not args.execute: return
    settings = read_env(args.env_file)
    limit = min(args.max_calls, int(settings.get('ZZZ_MAX_CALLS', '12')))
    client = CachedChatClient(base_url=settings.get('ZZZ_BASE_URL', ''), api_key=settings.get('ZZZ_API_KEY', ''),
        **model_config(settings, generation=True), cache_dir=args.cache, max_calls=limit,
        max_tokens=args.max_output_tokens, timeout=int(settings.get('ZZZ_TIMEOUT', '60')))
    base = Path(__file__).resolve().parent
    code = [Path(__file__).resolve(), *sorted((base/'generation').glob('*.py')),
        *sorted((base/'recommendation').glob('*.py')), *sorted((base/'profiling').glob('*.py')),
        *sorted((base/'submissions').glob('*.py')),base/'verify_java.py']
    identity = {'request_sha256': fingerprint(req), 'inputs': inputs, 'prompt_version': PROMPT_VERSION,
        'model': client.model, 'stage_models': client.stage_models, 'url': client.url, 'max_tokens': client.max_tokens, 'timeout': client.timeout,
        'max_content_revisions': 1, 'execution_policy': 'separate_explicit_approval',
        'implementation_sha256': {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code}}
    with RunStore(args.output, identity, limit, resume=args.resume, secret=client.key) as store:
        client.transport = RecordedTransport(store, send_chat); client.event_sink = store.api_event
        atomic_json(args.output/'manifest.json', {'schema': 'generation-run-v1', 'identity': identity, 'request_limit': limit})
        result = run_generation(store, client, req)
        print(json.dumps({k: result[k] for k in ('question_id', 'status', 'requests_reserved', 'ready_for_student')}, ensure_ascii=False, indent=2))
        if result['status'] == 'failed': raise RuntimeError('Generation failed; inspect --trace')


if __name__ == '__main__':
    try: main()
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        raise SystemExit(str(exc)) from None
