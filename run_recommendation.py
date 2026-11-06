"""Direct B recommendation; preview by default, --execute enables bounded API calls."""
import argparse
import hashlib
import json
from pathlib import Path

from profiling.api_client import CachedChatClient, read_env, send_chat, model_config
from profiling.run_store import RunStore, RecordedTransport, atomic_json, fingerprint
from recommendation.history import load_usage_history
from recommendation.context import prepare_context
from recommendation.comparison import PROMPT_VERSION
from recommendation.runner import run_recommendation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--usage-history', type=Path, help='Explicit question-usage-sources-v1 manifest')
    parser.add_argument('--student')
    parser.add_argument('--scope', nargs='+')
    parser.add_argument('--goals', nargs='*', default=[])
    parser.add_argument('--requirement', action='append', default=[])
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--output', type=Path, default=Path('runs/recommendation-001'))
    parser.add_argument('--cache', type=Path, default=Path('cache/recommendation'))
    parser.add_argument('--max-calls', type=int, choices=(1, 2), default=2)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--trace', action='store_true')
    args = parser.parse_args()
    if args.status or args.trace:
        if args.execute or args.resume:
            parser.error('Inspection cannot be combined with execution')
        print(json.dumps(RunStore.inspect(args.output, full=args.trace), ensure_ascii=False, indent=2))
        return
    if not args.student or not args.scope:
        parser.error('--student and --scope are required')
    if args.resume and not args.execute:
        parser.error('--resume requires --execute')
    inputs = {}

    def read(path, name):
        data = path.read_bytes()
        inputs[name] = {'file': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        return json.loads(data.decode('utf-8-sig'))

    catalog = read(args.catalog, 'catalog')
    profile = read(args.profile, 'profile') if args.profile else None
    history = load_usage_history(args.usage_history,args.student) if args.usage_history else None
    if args.usage_history: read(args.usage_history,'usage_history')
    context = prepare_context(catalog, student_id=args.student, allowed_concept_ids=args.scope,
        goal_concept_ids=args.goals, requirements=args.requirement, profile=profile, usage_history=history)
    print(json.dumps({'mode': 'execute' if args.execute else 'preview', 'strategy': 'B',
        'context_sha256': fingerprint(context), 'logical_calls': 1,
        'usage_history_supplied':history is not None,'question_usage_records':len(context.get('question_usage',[])),
        'request_limit_including_retries': args.max_calls}, ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        return
    settings = read_env(args.env_file)
    limit = min(args.max_calls, int(settings.get('ZZZ_MAX_CALLS', '12')))
    if limit < 1:
        raise ValueError('Request budget must be positive')
    client = CachedChatClient(base_url=settings.get('ZZZ_BASE_URL', ''), api_key=settings.get('ZZZ_API_KEY', ''),
        **model_config(settings), cache_dir=args.cache, max_calls=limit,
        max_tokens=int(settings.get('ZZZ_MAX_TOKENS', '1500')), timeout=int(settings.get('ZZZ_TIMEOUT', '60')))
    base = Path(__file__).resolve().parent
    code = [Path(__file__).resolve(), *sorted((base/'recommendation').glob('*.py')),
            *sorted((base/'profiling').glob('*.py')), *sorted((base/'submissions').glob('*.py')),
            base/'verify_java.py',base/'generation/workflow.py']
    identity = {'strategy': 'B', 'context_sha256': fingerprint(context), 'inputs': inputs,
        'prompt_version': PROMPT_VERSION, 'message_layout': 'single_user',
        'model': client.model, 'stage_models': client.stage_models, 'url': client.url, 'max_tokens': client.max_tokens, 'timeout': client.timeout,
        'implementation_sha256': {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code}}
    with RunStore(args.output, identity, limit, resume=args.resume, secret=client.key) as store:
        client.transport = RecordedTransport(store, send_chat)
        client.event_sink = store.api_event
        atomic_json(args.output/'manifest.json', {'schema': 'recommendation-run-v1',
            'identity': identity, 'request_limit': limit})
        report = run_recommendation(store, client, context)
        print(json.dumps({k: report[k] for k in ('recommendation_id', 'status', 'metrics')}, ensure_ascii=False, indent=2))
        if report['status'] == 'failed':
            raise RuntimeError('Recommendation failed; inspect --status/--trace. No generation request produced')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None
