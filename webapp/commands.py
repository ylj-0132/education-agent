"""Workbench operation metadata and validated CLI arguments; no execution."""
import os
import re
import sys
from pathlib import Path

SCRIPTS = {
    'profile': 'run_profile.py',
    'recommend': 'run_recommendation.py',
    'generate': 'generate_question.py',
    'update': 'update_profile.py',
    'submit': 'submit_answer.py',
    'verify_reference': 'verify_java.py',
    'verify_student': 'verify_submission.py',
}
FIELDS = {
    'profile': 'records concepts student verification_run limit max_calls',
    'recommend': 'catalog profile usage_history student scope goals requirement max_calls',
    'generate': 'recommendation_run catalog profile usage_history student scope targets requirement max_calls',
    'update': 'base_profile records submission_run verification_run catalog max_calls',
    'submit': 'generation_run submission',
    'verify_reference': 'generation_run cases approve_sha256',
    'verify_student': 'records submission_run record_id cases approve_sha256',
}
ARRAYS = {'scope', 'targets', 'goals', 'requirement', 'verification_run'}
PATHS = {
    'records', 'concepts', 'catalog', 'profile', 'usage_history',
    'recommendation_run', 'base_profile', 'submission_run', 'generation_run',
    'submission', 'cases', 'verification_run',
}


def operation_metadata():
    """Describe available inputs; CLI entry points still validate required inputs."""
    sources = {
        'profile': 'profiles',
        'base_profile': 'profiles',
        'generation_run': 'generations',
        'recommendation_run': 'recommendations',
        'submission_run': 'submission_runs',
        'verification_run': 'verification_runs',
        'records': 'record_files',
        'usage_history': 'usage_files',
    }
    operations = []
    for operation, field_names in FIELDS.items():
        fields = [
            dict(
                name=name,
                label=name,
                type='path' if name in PATHS else 'text',
                source=sources.get(name),
                required=False,
                multiple=name in ARRAYS,
            )
            for name in field_names.split()
        ]
        operations.append(dict(
            id=operation,
            label=operation,
            fields=fields,
            modes=['preview', 'save' if operation == 'submit' else 'execute'],
        ))
    return operations


def build_command(artifacts, data, job_id):
    """Validate one request and return (argv, relative_output), without running it."""
    op = data.get('operation')
    mode = data.get('mode')
    fields = data.get('fields', {})
    if op not in SCRIPTS or mode not in ('preview', 'save' if op == 'submit' else 'execute'):
        raise ValueError('Unknown operation or mode')
    if not isinstance(fields, dict) or set(fields) - set(FIELDS[op].split()):
        raise ValueError('Unsupported fields')
    if mode != 'preview' and data.get('confirm') is not True:
        raise ValueError('Explicit confirm=true is required')
    approval = data.get('approve_sha256', fields.get('approve_sha256'))
    if approval and (not isinstance(approval, str) or not re.fullmatch('[0-9a-fA-F]{64}', approval)):
        raise ValueError('Invalid approval SHA256')
    if (
        op.startswith('verify_') and mode == 'execute'
        and (not isinstance(approval, str) or not re.fullmatch('[0-9a-fA-F]{64}', approval))
    ):
        raise ValueError('Review source and cases, then provide approve_sha256')
    args = [sys.executable, str(artifacts.root / SCRIPTS[op])]
    if op.startswith('verify_'):
        args.extend([
            '--java-home',
            os.environ.get('LOCAL_JAVA_TEST_HOME', 'C:/Program Files/JetBrains/PyCharm 2024.3.4/jbr'),
        ])
    for key, value in fields.items():
        if value == '' or value == []:
            continue
        if key in ARRAYS:
            if (
                not isinstance(value, list) or len(value) > 100
                or any(not isinstance(x, str) or not x or len(x) > 4000 for x in value)
            ):
                raise ValueError('Invalid array field')
            values = value
            if key != 'requirement' and any(x.startswith('-') for x in values):
                raise ValueError('Array values cannot be CLI switches')
        else:
            if not isinstance(value, str) or len(value) > 4000 or '\x00' in value:
                raise ValueError('Fields must be strings')
            values = [value]
        if key in PATHS:
            values = [str(artifacts.safe_path(x)) for x in values]
            if any(not Path(x).exists() for x in values):
                raise ValueError('Input path does not exist')
            if any(
                Path(x).name.startswith('.') or Path(x).name in ('trace.json', 'request.json')
                for x in values
            ):
                raise ValueError('Private input forbidden')
            for x in values:
                p = Path(x)
                rel = p.relative_to(artifacts.root)
                if (
                    not rel.parts or rel.parts[0] not in ('runs', 'artifacts', 'config')
                    or (p.is_file() and p.suffix != '.json')
                ):
                    raise ValueError('Only JSON data and run directories may be used as inputs')
                if key.endswith('_run') and p.is_file():
                    # The UI selects a result file; the CLI consumes its run directory.
                    values[values.index(x)] = str(p.parent)
        flag = '--' + key.replace('_', '-')
        if key in ('requirement', 'verification_run'):
            # Bind each value to its flag so text cannot become a separate CLI switch.
            for x in values:
                args.append(flag + '=' + x)
        elif key not in ARRAYS and values[0].startswith('-'):
            args.append(flag + '=' + values[0])
        else:
            args.extend([flag, *values])
    if approval and 'approve_sha256' not in fields:
        args.extend(['--approve-sha256', approval])
    output = artifacts.root / 'runs' / ('workbench-' + job_id)
    args.extend(['--output', str(output)])
    if mode != 'preview':
        args.append('--save' if mode == 'save' else '--execute')
    return args, output.relative_to(artifacts.root).as_posix()
