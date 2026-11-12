"""Bounded, owned trusted CPU workers; never a raw-command MCP endpoint."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time
from collections.abc import Awaitable, Callable

from src.errors import fail
from src.models import Receipt, State, TERMINAL, utc_now
from src.policy import child_environment
from src.registry.process import identity, same_process


class Supervisor:
    def __init__(self, registry):
        self.registry = registry
        self.tasks: dict[str, asyncio.Task] = {}
        self._closing = False

    @staticmethod
    def _signal(birth):
        try:
            fd = os.pidfd_open(birth.pid)
            try:
                if same_process(birth):
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
            finally:
                os.close(fd)
        except ProcessLookupError:
            pass

    async def start_operation(self, job_id: str, operation: Callable[[], Awaitable[None]],
                              cancel: Callable[[], Awaitable[None]] | None = None):
        if self._closing:
            raise fail("unavailable", "job_start")
        owner = identity(os.getpid())
        if owner is None:
            raise fail("unavailable", "job_start")
        self.registry.bind_operation(job_id, owner)
        task = asyncio.create_task(self._operation(job_id, operation, cancel))
        self.tasks[job_id] = task
        task.add_done_callback(lambda done: self.tasks.pop(job_id, None))
        return self.registry.get_job(job_id)

    async def _operation(self, job_id, operation, cancel):
        child = asyncio.create_task(operation())
        cancelled = False
        try:
            while not child.done():
                if self.registry.cancellation_requested(job_id):
                    cancelled = True
                    if cancel is not None:
                        await asyncio.wait_for(cancel(), timeout=30)
                    child.cancel()
                    try:
                        await child
                    except asyncio.CancelledError:
                        pass
                    break
                await asyncio.wait({child}, timeout=0.1)
            if not cancelled:
                await child
            if self.registry.get_job(job_id).state not in TERMINAL:
                code = "uncertain_outcome" if cancel is not None or not cancelled else None
                self.registry.finish(job_id, State.INTERRUPTED if code else State.CANCELLED,
                    Receipt(job_id=job_id, observed_at=utc_now(), exit_code=None,
                        stop_reason="missing_operation_receipt" if code else "cancelled",
                        process_exited=True, descendants_exited=None, queue_seconds=None, held_seconds=None),
                    error=fail(code, "job_operation").envelope if code else None)
        except Exception:
            child.cancel()
            try:
                await child
            except (asyncio.CancelledError, Exception):
                pass
            if self.registry.get_job(job_id).state not in TERMINAL:
                self.registry.finish(job_id, State.INTERRUPTED, Receipt(job_id=job_id, observed_at=utc_now(),
                    exit_code=None, stop_reason="operation_failed", process_exited=True, descendants_exited=None,
                    queue_seconds=None, held_seconds=None), error=fail("uncertain_outcome", "job_operation").envelope)

    def job_directory(self, job_id: str) -> Path:
        self.registry.get_job(job_id)
        return self.registry.state_root / "workers" / job_id

    async def start(self, job_id: str, argv: list[str], *, cwd: Path,
                    wall_seconds: float, queue_timeout_seconds: float = 3600,
                    grace_seconds: float = 2, queued: bool = False,
                    cancellation: str = "group", trusted_read_environment: bool = False) -> object:
        """Trusted internal launch; adapter mode requires an ordered stop handler."""
        if self._closing or not argv or not Path(argv[0]).is_absolute() or not cwd.is_absolute():
            raise fail("invalid_argument", "job_start")
        if not (0 < wall_seconds <= 86400 and 0 < queue_timeout_seconds <= 86400 and 0 < grace_seconds <= 20):
            raise fail("invalid_argument", "job_start")
        if cancellation not in {"group", "adapter"}:
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
        if trusted_read_environment:
            # Only reviewed read adapters may request this; no token-variable inheritance.
            env.update({key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME", "GH_CONFIG_DIR") if key in os.environ})
        spec = dict(argv=argv, cwd=str(cwd), job_dir=str(root), parent_pid=os.getpid(),
                    wall_seconds=wall_seconds, queue_timeout_seconds=queue_timeout_seconds,
                    grace_seconds=grace_seconds, queued_monotonic=time.monotonic(), cancellation=cancellation)
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
        birth = None
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
                    if self.registry.cancellation_requested(job_id) and (root / "ready").exists() and same_process(birth):
                        self._signal(birth)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=0.1)
                    except TimeoutError:
                        pass
            self.reconcile(job_id)
        except Exception:
            if proc is not None and proc.returncode is None:
                if proc.stdin is not None:
                    proc.stdin.close()
                if birth is not None:
                    self._signal(birth)
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
            error = fail("timeout", "job_run").envelope if receipt.stop_reason == "timeout" else job.error
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
        if self.registry.latest_event(job_id, "operation_started") is not None:
            if birth is not None and not same_process(birth):
                self.reconcile(job_id)
            return {"cancel_requested": True, "job_id": job_id, "receipt": self.registry.receipt(job_id)}
        if birth is None and job_id not in self.tasks:
            receipt = Receipt(job_id=job_id, observed_at=utc_now(), exit_code=None,
                stop_reason="cancelled_before_start", process_exited=True, descendants_exited=True,
                queue_seconds=None, held_seconds=None)
            self.registry.finish(job_id, State.CANCELLED, receipt)
        elif birth is not None and (self.job_directory(job_id) / "ready").exists() and same_process(birth):
            # Birth identity and a still-live guardian are required; never pid-only kills.
            self._signal(birth)
        elif birth is not None and not same_process(birth):
            self.reconcile(job_id)
        return {"cancel_requested": True, "job_id": job_id, "receipt": self.registry.receipt(job_id)}

    async def close(self) -> None:
        self._closing = True
        pending = list(self.tasks.items())
        for job_id, _ in pending:
            await self.cancel(job_id)
        if pending:
            await asyncio.gather(*(task for _, task in pending))
