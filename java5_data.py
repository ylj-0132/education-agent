"""Offline Java_5 import. No model calls or submission execution.

The original data remains untouched. External annotations are not judge labels.
"""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

FIELDS = ('problem_id', 'question', 'desc', 'program', 'error_desc')
SCHEMA_VERSION = 'java5-evidence-v1'


def load_records(root):
    root = Path(root).resolve(strict=True)
    folders = sorted(p for p in root.glob('java_*') if p.is_dir())
    if not folders:
        raise ValueError('No java_* student directories found')
    records, questions = [], {}
    for folder in folders:
        split_ids = {}
        for split in ('train', 'test'):
            path = folder / f'{split}.json'
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            rows = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(rows, list) or not rows:
                raise ValueError(f'{path}: expected nonempty list')
            ids = set()
            for index, row in enumerate(rows):
                for field in FIELDS:
                    if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field].strip():
                        raise ValueError(f'{path}[{index}]: invalid {field}')
                pid = row['problem_id']
                if pid in ids:
                    raise ValueError(f'{path}: duplicate problem {pid}')
                ids.add(pid)
                if pid in questions and questions[pid] != row['question']:
                    raise ValueError(f'{path}[{index}]: question conflict for {pid}')
                questions[pid] = row['question']
                records.append({
                    'record_id': f'{folder.name}/{split}/{index}@{digest}',
                    'student_id': folder.name, 'split': split, 'problem_id': pid,
                    'question': row['question'], 'program': row['program'],
                    'annotations': {'desc': row['desc'], 'error_desc': row['error_desc'],
                                    'verification': 'unverified'},
                    'source': {'dataset': 'Java_5', 'file': path.relative_to(root).as_posix(),
                               'index': index, 'sha256': digest},
                })
            split_ids[split] = ids
        if split_ids['train'] & split_ids['test']:
            raise ValueError(f'{folder.name}: train/test overlap')
    return records


def review_batch(records, per_student=3):
    """Deterministic review queue, not a representative evaluation sample."""
    if per_student < 1:
        raise ValueError('per_student must be positive')
    users = defaultdict(list)
    for record in records:
        if record['split'] == 'train':
            users[record['student_id']].append(record)
    result = []
    for student in sorted(users):
        rows = sorted(users[student], key=lambda r: r['source']['index'])
        selected = []
        # Stratify only by annotation text; neither category is verified truth.
        for no_error in (True, False):
            candidates = [r for r in rows if
                          (r['annotations']['error_desc'].strip().lower() == 'no error.') == no_error]
            if candidates and len(selected) < per_student:
                selected.append(candidates[0])
        for row in rows:
            if len(selected) >= per_student:
                break
            if row not in selected:
                selected.append(row)
        result.extend(sorted(selected, key=lambda r: r['source']['index']))
    return result


def export_data(root, output, per_student=3):
    root, output = Path(root).resolve(strict=True), Path(output).resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError('Output must be outside the source data directory')
    if output.exists():
        raise FileExistsError(f'Output already exists: {output}')
    records = load_records(root)
    batch = review_batch(records, per_student)
    sources = {r['source']['file']: r['source']['sha256'] for r in records}
    version = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    counts = Counter(r['split'] for r in records)
    ids = {s: {r['problem_id'] for r in records if r['split'] == s} for s in ('train','test')}
    manifest = {
        'schema_version': SCHEMA_VERSION, 'dataset_version': version,
        'source_files': sources, 'counts': dict(counts),
        'students': len({r['student_id'] for r in records}),
        'unique_problems': len(ids['train'] | ids['test']),
        'cross_student_train_test_shared_problems': sorted(ids['train'] & ids['test']),
        'review_records': len(batch), 'review_unique_problems': len({r['problem_id'] for r in batch}),
        'review_policy': 'train only; per user first No error. and first other annotation, then source order',
        'model_calls': 0, 'submissions_executed': 0,
    }
    payloads = {'manifest.json': manifest, 'review-batch.json': batch}
    for split in ('train','test'):
        selected = [r for r in records if r['split'] == split]
        payloads[f'{split}-questions.json'] = [
            {'problem_id': pid, 'question': next(r['question'] for r in selected if r['problem_id']==pid)}
            for pid in sorted(ids[split])]
        payloads[f'{split}-evidence.json'] = [{k:v for k,v in r.items() if k!='question'} for r in selected]
    # Validate everything before creating the destination. Never overwrite a prior run.
    output.mkdir(parents=True, exist_ok=False)
    for name, payload in payloads.items():
        (output/name).write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--per-student', type=int, default=3)
    args = parser.parse_args()
    try:
        result = export_data(args.data_root, args.output, args.per_student)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    print(json.dumps({k:result[k] for k in ('counts','students','unique_problems','review_records','review_unique_problems')},ensure_ascii=False))


if __name__ == '__main__':
    main()
