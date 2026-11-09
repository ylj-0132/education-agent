"""Application entry point shared by the existing local HTTP routes."""
import secrets
import uuid
from pathlib import Path

from .artifacts import ArtifactStore
from .commands import build_command, operation_metadata
from .jobs import JobQueue, run_command  # Keep the existing runner import available.


class WorkbenchService:
    """Coordinate result access, command validation and serial task execution."""

    def __init__(self, root, runner=None):
        self.root = Path(root).resolve()
        self.csrf_token = secrets.token_urlsafe(32)
        self._artifacts = ArtifactStore(self.root)
        self._queue = JobQueue(self.root, runner)

    @property
    def runner(self):
        return self._queue.runner

    @runner.setter
    def runner(self, value):
        self._queue.runner = value

    def safe_path(self, value):
        return self._artifacts.safe_path(value)

    def inventory(self):
        return self._artifacts.inventory()

    def artifact(self, value):
        return self._artifacts.artifact(value)

    def bootstrap(self):
        groups, _ = self.inventory()
        catalog = (
            self.artifact('config/java-basics.json')
            if (self.root / 'config/java-basics.json').exists()
            else {}
        )
        return dict(
            csrf_token=self.csrf_token,
            catalog=catalog,
            operations=operation_metadata(),
            **groups,
        )

    def command(self, data, job_id):
        return build_command(self._artifacts, data, job_id)

    def launch(self, data):
        if not isinstance(data, dict):
            raise ValueError('JSON object required')
        job_id = uuid.uuid4().hex
        args, output = self.command(data, job_id)
        return self._queue.start(job_id, data['operation'], data['mode'], args, output)

    def get_job(self, job_id):
        return self._queue.get_job(job_id)

    def jobs(self):
        return self._queue.jobs()

    def save_submission_draft(self, answer):
        return self._artifacts.save_submission_draft(answer)
