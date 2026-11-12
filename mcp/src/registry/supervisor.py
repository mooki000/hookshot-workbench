"""Bounded, owned trusted CPU workers; never a raw-command MCP endpoint."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

from src.errors import fail
from src.models import Receipt, State, TERMINAL, utc_now
from src.policy import child_environment
from src.registry.process import identity, same_process


class Supervisor:
    def __init__(self, registry):
        self.registry = registry
        self.tasks: dict[str, asyncio.Task] = {}
        self._closing = False

    def job_directory(self, job_id: str) -> Path:
        self.registry.get_job(job_id)
        return self.registry.state_root / "workers" / job_id

    async def start(self, job_id: str, argv: list[str], *, cwd: Path,
                    wall_seconds: float, queue_timeout_seconds: float = 3600,
                    grace_seconds: float = 2, queued: bool = False) -> object:
        """For reviewed CPU code only. Gated game teardown needs its own adapter."""
        if self._closing or not argv or not Path(argv[0]).is_absolute() or not cwd.is_absolute():
            raise fail("invalid_argument", "job_start")
        if not (0 < wall_seconds <= 86400 and 0 < queue_timeout_seconds <= 86400 and 0 < grace_seconds <= 20):
            raise fail("invalid_argument", "job_start")
        job = self.registry.get_job(job_id)
        if job.state != State.QUEUED or self.registry.process(job_id)[0] is not None:
            raise fail("refused_by_policy", "job_start")
        root = self.job_directory(job_id)
        root.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            # A failed/uncertain launch is not permission to replay it.
            raise fail("uncertain_outcome", "job_start") from None
        env = child_environment(root)
        spec = dict(argv=argv, cwd=str(cwd), job_dir=str(root), parent_pid=os.getpid(),
                    wall_seconds=wall_seconds, queue_timeout_seconds=queue_timeout_seconds,
                    grace_seconds=grace_seconds)
        payload = json.dumps(spec).encode() + b"\n"
        if len(payload) > 65536:
            raise fail("invalid_argument", "job_start")
        # Keep startup independent of cancellation of the submitting tool call.
        task = asyncio.create_task(self._launch(job_id, payload, env, root, queued))
        self.tasks[job_id] = task
        task.add_done_callback(lambda done: self.tasks.pop(job_id, None))
        await asyncio.shield(self._await_bound(job_id, task))
        return self.registry.get_job(job_id)

    async def _await_bound(self, job_id, task):
        while self.registry.process(job_id)[0] is None and not task.done():
            await asyncio.sleep(0.01)
        if task.done():
            await task

    async def _launch(self, job_id, payload, env, root, queued):
        proc = None
        try:
            with (root / "stdout.log").open("xb") as out, (root / "stderr.log").open("xb") as err:
                proc = await asyncio.create_subprocess_exec(sys.executable, "-I", str(Path(__file__).with_name("worker.py")),
                    stdin=asyncio.subprocess.PIPE, stdout=out, stderr=err,
                    start_new_session=True, env=env)
                birth = identity(proc.pid)
                parent = identity(os.getpid())
                if birth is None or parent is None:
                    raise fail("uncertain_outcome", "job_start")
                self.registry.bind_process(job_id, birth, parent)
                if not queued:
                    self.mark_admitted(job_id)
                proc.stdin.write(payload)
                await proc.stdin.drain()
                proc.stdin.close()
                while proc.returncode is None:
                    if self.registry.cancellation_requested(job_id) and same_process(birth):
                        os.killpg(birth.process_group, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=0.1)
                    except TimeoutError:
                        pass
            self.reconcile(job_id)
        except Exception:
            if proc is not None and proc.returncode is None:
                birth = identity(proc.pid)
                if birth is not None:
                    os.killpg(birth.process_group, signal.SIGTERM)
                await proc.wait()
            if self.registry.get_job(job_id).state not in TERMINAL:
                self.registry.finish(job_id, State.INTERRUPTED, Receipt(job_id=job_id, observed_at=utc_now(),
                    exit_code=None, stop_reason="supervisor_failed", process_exited=proc is None or proc.returncode is not None,
                    descendants_exited=None, queue_seconds=None, held_seconds=None),
                    error=fail("uncertain_outcome", "job_start").envelope)

    def mark_admitted(self, job_id: str) -> None:
        root = self.job_directory(job_id)
        try:
            with (root / "admitted").open("x") as stream:
                stream.write(str(time.monotonic()))
        except FileExistsError:
            raise fail("refused_by_policy", "job_admission") from None
        self.registry.update_job(job_id, state=State.RUNNING, phase="running", started_at=utc_now())
        self.registry.append_event(job_id, "admitted", {})

    def reconcile(self, job_id: str):
        job = self.registry.get_job(job_id)
        if job.state in TERMINAL:
            return job
        birth, parent = self.registry.process(job_id)
        if birth is not None and same_process(birth):
            return job
        path = self.job_directory(job_id) / "receipt.json"
        try:
            with path.open("rb") as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError
            receipt = Receipt(job_id=job_id, observed_at=utc_now(), **json.loads(raw))
            state = (State.CANCELLED if receipt.stop_reason == "cancelled" else
                     State.FAILED if receipt.stop_reason == "timeout" or receipt.exit_code != 0 else State.COMPLETED)
            error = fail("timeout", "job_run").envelope if receipt.stop_reason == "timeout" else None
            return self.registry.finish(job_id, state, receipt, error=error)
        except (OSError, ValueError):
            if birth is None:
                return job
            return self.registry.finish(job_id, State.INTERRUPTED, Receipt(job_id=job_id, observed_at=utc_now(),
                exit_code=None, stop_reason="missing_receipt", process_exited=True,
                descendants_exited=None, queue_seconds=None, held_seconds=None),
                error=fail("uncertain_outcome", "job_read").envelope)

    async def cancel(self, job_id: str) -> dict:
        job = self.registry.get_job(job_id)
        if job.state in TERMINAL:
            return {"cancel_requested": False, "job_id": job_id, "receipt": self.registry.receipt(job_id)}
        birth, parent = self.registry.process(job_id)
        self.registry.request_cancel(job_id)
        if birth is None and job_id not in self.tasks:
            receipt = Receipt(job_id=job_id, observed_at=utc_now(), exit_code=None,
                stop_reason="cancelled_before_start", process_exited=True, descendants_exited=True,
                queue_seconds=None, held_seconds=None)
            self.registry.finish(job_id, State.CANCELLED, receipt)
        elif birth is not None and same_process(birth):
            # Birth identity and a still-live guardian are required; never pid-only kills.
            os.killpg(birth.process_group, signal.SIGTERM)
        else:
            self.reconcile(job_id)
        return {"cancel_requested": True, "job_id": job_id, "receipt": self.registry.receipt(job_id)}

    async def close(self) -> None:
        self._closing = True
        pending = list(self.tasks.items())
        for job_id, _ in pending:
            await self.cancel(job_id)
        if pending:
            await asyncio.gather(*(task for _, task in pending))
