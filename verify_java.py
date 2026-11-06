"""Preview source and tests; explicit hash approval enables non-sandboxed local Java."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from generation.local_java import prepare_review, verify_local
from profiling.run_store import RunStore, fingerprint


def load_question(path):
    state = RunStore.inspect(path, full=True)
    report = json.loads((path/'generation.json').read_text(encoding='utf-8'))
    if state['status'] != 'completed' or report.get('execution_status') != 'completed' or report.get('run_id') != state['run_id']:
        raise ValueError('Generation must be completed before local review')
    if report.get('status') != 'needs_review' or not report.get('versions'):
        raise ValueError('Expected a candidate awaiting review; rejected or split tasks are not executable')
    version = report['versions'][-1]; q = version['question']; n = version['version']
    saved = state['steps'].get(f'v{n}.generate', {})
    if saved.get('status') != 'succeeded' or saved.get('result') != q or version['question_sha256'] != fingerprint(q):
        raise ValueError('Question changed after generation')
    saved_review = state['steps'].get(f'v{n}.review', {})
    if saved_review.get('status') != 'succeeded' or saved_review.get('result') != version['review']:
        raise ValueError('Review mismatch')
    if report['question_id'] != state['generation_round']['question_id']:
        raise ValueError('Question identity mismatch')
    return q, report['question_id']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation-run', type=Path)
    parser.add_argument('--cases', type=Path)
    parser.add_argument('--java-home', type=Path)
    parser.add_argument('--output', type=Path, default=Path('runs/java-verification-001'))
    parser.add_argument('--approve-sha256', help='Supply only after reviewing the printed source and test expectations')
    for opt in ('execute', 'resume', 'status', 'trace'): parser.add_argument('--'+opt, action='store_true')
    args = parser.parse_args()
    if args.status or args.trace:
        if args.execute or args.resume: parser.error('Inspection cannot be combined with execution')
        print(json.dumps(RunStore.inspect(args.output, full=args.trace), ensure_ascii=False, indent=2)); return
    if not args.generation_run or not args.cases: parser.error('--generation-run and --cases required')
    if args.resume and not args.execute: parser.error('--resume requires --execute')
    if args.execute and not args.approve_sha256: parser.error('Review the preview first; --approve-sha256 is required')
    q, question_id = load_question(args.generation_run)
    cases = json.loads(args.cases.read_text(encoding='utf-8-sig'))
    tools = {name: str(args.java_home/'bin'/(name+'.exe')) if args.java_home else shutil.which(name)
             for name in ('java', 'javac')}
    if not all(tools.values()): raise ValueError('Java/Javac not found; specify --java-home')
    package = prepare_review(q, cases, tools, question_id=question_id)
    print(json.dumps({'mode': 'execute' if args.execute else 'preview', 'review': package,
        'notice': 'Local execution is not sandboxed. Review source and independently verify expected outputs before approval.'}, ensure_ascii=False, indent=2), flush=True)
    if not args.execute: return
    if args.approve_sha256 != package['approval_sha256']: raise ValueError('Approval mismatch; review current preview')
    base = Path(__file__).resolve().parent
    files = [Path(__file__).resolve(), base/'generation/local_java.py', base/'profiling/run_store.py']
    identity = {'approval_sha256': package['approval_sha256'], 'generation_run': str(args.generation_run.resolve()),
        'implementation_sha256': {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with RunStore(args.output, identity, 1, resume=args.resume) as store:
        result = verify_local(store, package, args.approve_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result['status'] != 'tests_passed': raise RuntimeError('Local tests did not pass; inspect verification.json and trace')


if __name__ == '__main__':
    try: main()
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        raise SystemExit(str(exc)) from None
