"""Explicitly reviewed local Java tests. Resource guards, NOT a security sandbox."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from profiling.run_store import atomic_json, fingerprint, stamp

LIMITS = {'compile_seconds': 20, 'case_seconds': 3, 'output_bytes': 65536,
          'heap_mb': 128, 'metaspace_mb': 64, 'max_cases': 20}


def prepare_review(question, cases, tools, *, question_id):
    if not isinstance(question, dict) or not isinstance(question.get('reference_code'), str) or not question['reference_code'].strip():
        raise ValueError('Reference source required')
    if len(question['reference_code'].encode()) > 200000: raise ValueError('Source too large')
    if not isinstance(cases, list) or not 1 <= len(cases) <= LIMITS['max_cases']:
        raise ValueError('Provide 1..20 independently checked test cases')
    for c in cases:
        if (not isinstance(c, dict) or set(c) != {'input', 'expected_output', 'basis'}
                or any(not isinstance(v, str) for v in c.values()) or not c['basis'].strip()
                or any(len(v.encode()) > 65536 for v in c.values())):
            raise ValueError('Each case requires bounded input, expected_output and independent basis')
    binaries = {}
    for name in ('java', 'javac'):
        p = Path(tools[name]).resolve(strict=True)
        binaries[name] = {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    data = {'schema': 'java-local-approval-v1', 'question_id': question_id,
        'question': deepcopy(question), 'question_sha256': fingerprint(question),
        'cases': deepcopy(cases), 'tools': binaries, 'limits': dict(LIMITS),
        'policy': 'reviewed_local_execution_not_sandbox',
        'comparison': 'exact_after_CRLF_normalization'}
    return {**data, 'approval_sha256': fingerprint(data)}


def minimal_env(work, bin_dir):
    env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'SYSTEMROOT') if k in os.environ}
    env.update(TEMP=str(work), TMP=str(work), TMPDIR=str(work),
        PATH=str(bin_dir)+os.pathsep+str(Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32'))
    return env


def _stop(proc):
    if proc.poll() is not None: return
    if os.name == 'nt':
        taskkill = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/taskkill.exe'
        try:
            subprocess.run([str(taskkill), '/PID', str(proc.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, shell=False)
        except (OSError, subprocess.TimeoutExpired): pass
    if proc.poll() is None: proc.kill()


def bounded_process(command, *, cwd, env, stdin, timeout, output_limit):
    """Bound captured output and wall time; child-tree termination is best effort."""
    started = time.monotonic(); buffers = [bytearray(), bytearray()]
    lock = threading.Lock(); exceeded = threading.Event(); total = 0
    with tempfile.TemporaryFile() as inp:
        inp.write(stdin.encode('utf-8')); inp.seek(0)
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=inp,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        def reader(stream, index):
            nonlocal total
            try:
                while True:
                    block = stream.read(4096)
                    if not block: break
                    with lock:
                        remaining = max(0, output_limit-total)
                        buffers[index].extend(block[:remaining]); total += min(len(block), remaining)
                        if len(block) > remaining: exceeded.set()
            except (OSError, ValueError): pass
        threads = [threading.Thread(target=reader, args=(stream, i), daemon=True)
                   for i, stream in enumerate((proc.stdout, proc.stderr))]
        for thread in threads: thread.start()
        status = 'completed'
        try:
            while proc.poll() is None or any(t.is_alive() for t in threads):
                if exceeded.is_set(): status = 'output_limit'; _stop(proc); break
                if time.monotonic()-started >= timeout: status = 'timeout'; _stop(proc); break
                time.sleep(.01)
            proc.wait(timeout=5)
        except BaseException:
            _stop(proc); raise
        finally:
            for thread in threads: thread.join(timeout=.3)
            # Avoid blocking close if an escaped descendant still owns a pipe.
            for stream, thread in zip((proc.stdout, proc.stderr), threads):
                if not thread.is_alive(): stream.close()
        if exceeded.is_set(): status = 'output_limit'
        with lock:
            out = [bytes(b).decode('utf-8', errors='replace') for b in buffers]
        return {'status': status, 'returncode': proc.returncode, 'stdout': out[0], 'stderr': out[1],
            'elapsed_seconds': time.monotonic()-started}


def verify_local(store, package, approved_sha256, *, runner=bounded_process):
    data = {k: v for k, v in package.items() if k != 'approval_sha256'}
    if not approved_sha256 or approved_sha256 != fingerprint(data) or approved_sha256 != package['approval_sha256']:
        raise ValueError('Explicit approval must match source, cases, tools and limits')
    old_hash = store.state.setdefault('local_approval_sha256', approved_sha256)
    if old_hash != approved_sha256: raise ValueError('Approval changed; use a new task')
    for tool in package['tools'].values():
        if hashlib.sha256(Path(tool['path']).read_bytes()).hexdigest() != tool['sha256']:
            raise ValueError('Java tool changed after approval')
    key = 'local.compile_and_test'
    old = store.state['steps'].get(key)
    base = {'schema': 'java-local-result-v1', 'run_id': store.state['run_id'],
        'question_id': package['question_id'], 'question_sha256': package['question_sha256'],
        'approval_sha256': approved_sha256, 'sandboxed': False, 'ready_for_student': False}
    store.state['status'] = 'running'; store.commit('local_verification_started', approval_sha256=approved_sha256)
    try:
        atomic_json(store.path/'approval.json', package)
        if old and old['status'] in ('running', 'interrupted'):
            result = {**base, 'status': 'execution_unknown', 'reason': 'Inspect trace; interrupted local execution is not automatically repeated'}
        else:
            saved = store.start_step(key, {'approval_sha256': approved_sha256})
            if saved is not None:
                result = saved; store.commit('checkpoint_reused')
            elif old and old['status'] == 'failed':
                result = {**base, 'status': 'execution_failed', 'error': old.get('error')}
                store.state['steps'][key] = old
            else:
                result = {**base, 'status': 'compile_failed', 'cases': [], 'started_at': stamp()}
                with tempfile.TemporaryDirectory(prefix='education-java-') as tmp:
                    work = Path(tmp); (work/'Main.java').write_text(package['question']['reference_code'], encoding='utf-8')
                    tools = package['tools']; limits = package['limits']
                    env = minimal_env(work, Path(tools['java']['path']).parent)
                    def invoke(command, inp, seconds, label):
                        store.commit('local_process_started', label=label, command=command)
                        value = runner(command, cwd=work, env=env, stdin=inp, timeout=seconds, output_limit=limits['output_bytes'])
                        store.commit('local_process_finished', label=label, result=value)
                        return value
                    compile_result = invoke([tools['javac']['path'], '-J-Xmx128m', '-proc:none', '-encoding', 'UTF-8',
                        '-classpath', str(work), '-d', str(work), str(work/'Main.java')], '', limits['compile_seconds'], 'compile')
                    result['compile'] = compile_result
                    if compile_result['status'] == 'completed' and compile_result['returncode'] == 0:
                        result['status'] = 'tests_passed'
                        for i, case in enumerate(package['cases']):
                            execution = invoke([tools['java']['path'], '-Xmx128m', '-XX:MaxMetaspaceSize=64m',
                                '-Dfile.encoding=UTF-8', '-cp', str(work), 'Main'], case['input'], limits['case_seconds'], 'case:'+str(i))
                            passed = (execution['status'] == 'completed' and execution['returncode'] == 0
                                and execution['stdout'].replace('\r\n', '\n') == case['expected_output'].replace('\r\n', '\n'))
                            result['cases'].append({'index': i, 'passed': passed, 'execution': execution, 'basis': case['basis']})
                            if not passed:
                                result['status'] = 'tests_failed'; break
                store.succeed_step(result)
        atomic_json(store.path/'verification.json', result)
        store.state['status'] = 'interrupted' if result['status'] == 'execution_unknown' else 'completed'
        store.commit('local_verification_finished', outcome=result['status'])
        return result
    except BaseException as exc:
        if store.active_step and store.state['steps'][store.active_step]['status'] == 'running':
            if isinstance(exc, Exception): store.fail_step(exc)
            else: store.state['steps'][store.active_step]['status'] = 'interrupted'
        store.state['status'] = 'interrupted'; store.commit('local_verification_error', error=store.error(exc)); raise
    finally:
        store.export()
