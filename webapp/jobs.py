"""Serial local tasks with bounded, redacted logs and an injectable runner."""
import os
import re
import subprocess
import threading
from pathlib import Path

LIMIT = 24000


def scrub(value):
    """Limit the log exposed over HTTP and mask common credential formats."""
    text = str(value)
    text = re.sub(
        r'(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s"\r\n]+',
        r'\1[redacted]', text,
    )
    text = re.sub(
        r'(?i)((?:api[_-]?key|token|secret|password)\s*["\']?\s*[:=]\s*["\']?)[^\s,"\'\r\n]+',
        r'\1[redacted]', text,
    )
    return re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)[:LIMIT]


def run_command(args, cwd):
    """Run the prepared CLI without a shell, draining both output streams."""
    environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    process = subprocess.Popen(
        args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        shell=False, env=environment,
    )
    buffers = [bytearray(), bytearray()]

    def drain(stream, buffer):
        with stream:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                # Continue draining past the capture limit to avoid a full-pipe deadlock.
                buffer.extend(chunk[:max(0, LIMIT * 4 - len(buffer))])

    readers = [
        threading.Thread(target=drain, args=(stream, buffer), daemon=True)
        for stream, buffer in zip((process.stdout, process.stderr), buffers)
    ]
    for reader in readers:
        reader.start()
    try:
        code = process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        code = 124
    for reader in readers:
        reader.join(timeout=2)
    return code, *(bytes(b).decode('utf-8', errors='replace') for b in buffers)


class JobQueue:
    """In-memory status only; persisted CLI checkpoints remain authoritative."""

    def __init__(self, root, runner=None):
        self.root = Path(root).resolve()
        self.runner = runner or run_command
        self._jobs = {}
        self._lock = threading.Lock()
        self._busy = False

    def start(self, job_id, operation, mode, args, output):
        """Start one validated task; reject concurrent work rather than queueing it."""
        with self._lock:
            if self._busy:
                raise RuntimeError('Another task is running')
            self._busy = True
            job = dict(
                id=job_id, status='queued', operation=operation, mode=mode,
                output=output, log='', error=None,
            )
            self._jobs[job_id] = job
            # Retain the active job and only the most recent completed task summaries.
            while len(self._jobs) > 50:
                self._jobs.pop(next(iter(self._jobs)))

        def work():
            try:
                with self._lock:
                    job['status'] = 'running'
                code, out, err = self.runner(args, self.root)
                with self._lock:
                    job.update(
                        status='succeeded' if code == 0 else 'failed',
                        log=scrub(out),
                        error=scrub(err) or (f'CLI exited with status {code}' if code else None),
                    )
            except Exception:
                with self._lock:
                    job.update(
                        status='failed',
                        error='Local task failed; inspect local terminal and result files',
                    )
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=work, daemon=True).start()
        return self.get_job(job_id)

    def get_job(self, job_id):
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError('Unknown job')
            return dict(self._jobs[job_id])

    def jobs(self):
        with self._lock:
            return [dict(x) for x in reversed(list(self._jobs.values()))]
