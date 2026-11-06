"""Three teaching cases, paired message layouts, A/B comparison. Preview by default."""
import argparse
import hashlib
import json
from pathlib import Path

from profiling.api_client import read_env
from recommendation.sweep import build_cases, run_sweep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--profile', type=Path, default=Path('runs/java5-key-006/profile.json'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--output', type=Path, default=Path('runs/recommendation-sweep-001'))
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.resume and not args.execute:
        parser.error('--resume requires --execute')
    sources = {}

    def read(path, name):
        data = path.read_bytes()
        sources[name] = {'file': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        return json.loads(data.decode('utf-8-sig'))

    cases = build_cases(read(args.catalog, 'catalog'), read(args.profile, 'profile'))
    print(json.dumps({'mode': 'execute' if args.execute else 'preview',
        'cases': [{k: c[k] for k in ('case_id', 'origin', 'review_focus')} for c in cases],
        'layouts': ['single_user', 'split_roles'], 'normal_calls': 18, 'maximum_calls': 36},
        ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        return
    report = run_sweep(cases, read_env(args.env_file), args.output, resume=args.resume, source_inputs=sources)
    print(json.dumps({k: report[k] for k in ('status', 'requests', 'reported_usage', 'role_probe')}, ensure_ascii=False, indent=2))
    if report['status'] != 'completed':
        raise RuntimeError('Suite partial; failed cells remain recorded for analysis')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
