"""Offline concept-bundle context preparation; no model or automatic selection."""
import argparse
import hashlib
import json
from pathlib import Path

from profiling.run_store import atomic_json
from recommendation.context import prepare_context, build_generation_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--student', required=True)
    parser.add_argument('--scope', nargs='+', required=True)
    parser.add_argument('--goals', nargs='*', default=[])
    parser.add_argument('--requirement', action='append', default=[])
    parser.add_argument('--select', nargs='+', help='Explicit caller targets, not an automatic recommendation')
    parser.add_argument('--reason')
    parser.add_argument('--reason-evidence', nargs='*', default=[])
    parser.add_argument('--output', type=Path, help='New output directory; omitted means read-only preview')
    args = parser.parse_args()
    if args.select is None and (args.reason is not None or args.reason_evidence):
        parser.error('--reason and --reason-evidence require --select')
    inputs = {}

    def read(path, name):
        data = path.read_bytes()
        inputs[name] = {'file': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        return json.loads(data.decode('utf-8-sig'))

    catalog = read(args.catalog, 'catalog')
    profile = read(args.profile, 'profile') if args.profile else None
    context = prepare_context(catalog, student_id=args.student, allowed_concept_ids=args.scope,
                              goal_concept_ids=args.goals, requirements=args.requirement, profile=profile)
    request = None
    if args.select is not None:
        request = build_generation_request(context, selected_concept_ids=args.select,
                                           reason=args.reason, evidence_ids=args.reason_evidence)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        atomic_json(args.output/'context.json', context)
        if request is not None:
            atomic_json(args.output/'generation-request.json', request)
        atomic_json(args.output/'manifest.json', {
            'schema': 'recommendation-preparation-v1', 'status': 'prepared',
            'network_calls': 0, 'inputs': inputs,
            'request': {'student_id': args.student, 'scope': args.scope, 'goals': args.goals,
                        'requirements': args.requirement, 'explicit_selection': args.select,
                        'reason': args.reason, 'reason_evidence_ids': args.reason_evidence},
            'implementation_sha256': {
                str(p.relative_to(Path(__file__).parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [Path(__file__), Path(__file__).parent/'recommendation/context.py']}})
    print(json.dumps({'status': context['status'], 'scope_size': len(args.scope),
                      'evidence_records': len(context['evidence']), 'network_calls': 0,
                      'explicit_handoff': request is not None,
        'selection_policy': context['selection_policy']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
