"""Preview a paired comparison; --execute explicitly enables bounded paid calls."""
import argparse
import hashlib
import json
from pathlib import Path

from profiling.api_client import CachedChatClient, read_env, send_chat
from profiling.run_store import RunStore, RecordedTransport, atomic_json, fingerprint
from recommendation.context import prepare_context
from recommendation.comparison import PROMPT_VERSION, run_comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--student')
    parser.add_argument('--scope', nargs='+')
    parser.add_argument('--goals', nargs='*', default=[])
    parser.add_argument('--requirement', action='append', default=[])
    parser.add_argument('--candidate-count', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--order', choices=('A-first', 'B-first'), default='A-first')
    parser.add_argument('--message-layout', choices=('single_user', 'split_roles'), default='single_user')
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--output', type=Path, default=Path('runs/recommendation-comparison-001'))
    parser.add_argument('--cache', type=Path, default=Path('cache/recommendation'))
    parser.add_argument('--max-calls', type=int, choices=range(1, 7), default=6)
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
        parser.error('--student and --scope are required for a comparison')
    if args.resume and not args.execute:
        parser.error('--resume requires --execute')
    inputs = {}

    def read(path, name):
        data = path.read_bytes()
        inputs[name] = {'file': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        return json.loads(data.decode('utf-8-sig'))

    catalog = read(args.catalog, 'catalog')
    profile = read(args.profile, 'profile') if args.profile else None
    context = prepare_context(catalog, student_id=args.student, allowed_concept_ids=args.scope,
                              goal_concept_ids=args.goals, requirements=args.requirement, profile=profile)
    preview = {'mode': 'execute' if args.execute else 'preview', 'context_sha256': fingerprint(context),
               'candidate_count_limit': args.candidate_count, 'order': args.order, 'message_layout': args.message_layout,
               'logical_calls': {'A': 2, 'B': 1}, 'request_limit_including_retries': args.max_calls,
               'equal_compute_budget': False}
    print(json.dumps(preview, ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        return
    settings = read_env(args.env_file)
    limit = min(args.max_calls, int(settings.get('ZZZ_MAX_CALLS', '12')))
    client = CachedChatClient(base_url=settings.get('ZZZ_BASE_URL', ''), api_key=settings.get('ZZZ_API_KEY', ''),
                              model=settings.get('ZZZ_MODEL', ''), cache_dir=args.cache, max_calls=limit,
                              max_tokens=int(settings.get('ZZZ_MAX_TOKENS', '1500')),
                              timeout=int(settings.get('ZZZ_TIMEOUT', '60')))
    base = Path(__file__).parent
    code = [Path(__file__), *sorted((base/'recommendation').glob('*.py')),
            *sorted((base/'profiling').glob('*.py'))]
    identity = {'context_sha256': fingerprint(context), 'inputs': inputs, 'prompt_version': PROMPT_VERSION,
                'model': client.model, 'url': client.url, 'max_tokens': client.max_tokens, 'timeout': client.timeout,
                'candidate_count': args.candidate_count, 'order': args.order, 'message_layout': args.message_layout,
                'implementation_sha256': {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code}}
    with RunStore(args.output, identity, limit, resume=args.resume, secret=client.key) as store:
        client.transport = RecordedTransport(store, send_chat)
        client.event_sink = store.api_event
        atomic_json(args.output/'manifest.json', {'schema': 'recommendation-comparison-v1',
                    'identity': identity, 'request_limit': limit, 'equal_compute_budget': False})
        report = run_comparison(store, client, context, candidate_count=args.candidate_count, order=args.order, message_layout=args.message_layout)
        print(json.dumps({'status': report['status'], 'arms': {
            k: {x: v[x] for x in ('status', 'requests', 'usage', 'failed_or_rejected_requests')}
            for k, v in report['arms'].items()}}, ensure_ascii=False, indent=2))
        if report['status'] != 'completed':
            raise RuntimeError('Comparison partial; inspect status and trace, do not infer a winner')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None
