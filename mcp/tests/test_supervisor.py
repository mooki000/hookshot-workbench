import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from src.errors import WorkbenchError
from src.models import State
from src.registry.process import identity
from src.tools.status import wait_job


async def eventually(predicate, timeout=5):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "observed-state deadline exceeded"
        await asyncio.sleep(0.02)


async def test_supervisor_receipt_and_deadline(deps, tmp_path):
    script = tmp_path / "fixture.py"
    script.write_text("print('synthetic child output')\n")
    await deps.open()
    try:
        job = deps.registry.create_job("fixture")
        await deps.supervisor.start(job.job_id, [sys.executable, str(script)], cwd=tmp_path, wall_seconds=2)
        result = await wait_job(deps, job.job_id, 5)
        assert result.job.state == State.COMPLETED
        assert result.receipt.exit_code == 0 and result.receipt.descendants_exited
        assert result.receipt.queue_seconds >= 0 and result.receipt.held_seconds >= 0
        with pytest.raises(WorkbenchError):
            await deps.supervisor.start(job.job_id, [sys.executable, str(script)], cwd=tmp_path, wall_seconds=2)
        script.write_text("import time; time.sleep(60)\n")
        timed = deps.registry.create_job("fixture")
        await deps.supervisor.start(timed.job_id, [sys.executable, str(script)], cwd=tmp_path, wall_seconds=0.1, grace_seconds=0.1)
        result = await wait_job(deps, timed.job_id, 5)
        assert result.job.state == State.FAILED and result.receipt.stop_reason == "timeout"
    finally:
        await deps.close()


@pytest.mark.parametrize("escape", [False, True])
async def test_cancel_child_and_grandchild(deps, tmp_path, escape):
    script = tmp_path / "tree.py"
    pidfile = tmp_path / "pids.txt"
    grandchild = "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); " + ("os.setsid(); " if escape else "") + "time.sleep(60)"
    script.write_text("import os,signal,subprocess,sys,time\n"
                      "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                      f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}])\n"
                      f"open({str(pidfile)!r},'w').write(str(os.getpid())+' '+str(p.pid))\n"
                      "time.sleep(60)\n")
    await deps.open()
    try:
        job = deps.registry.create_job("fixture")
        await deps.supervisor.start(job.job_id, [sys.executable, str(script)], cwd=tmp_path, wall_seconds=20, grace_seconds=0.1)
        await eventually(pidfile.exists)
        children = [int(pid) for pid in pidfile.read_text().split()]
        assert all(identity(pid) is not None for pid in children)
        cancelled = await deps.supervisor.cancel(job.job_id)
        assert cancelled["cancel_requested"]
        result = await wait_job(deps, job.job_id, 5)
        assert result.job.state == State.CANCELLED and result.receipt.descendants_exited
        assert all(identity(pid) is None for pid in children)
        assert all(not Path(f"/proc/{pid}").exists() for pid in children), "guardian must reap zombies"
    finally:
        await deps.close()


async def test_queued_clock_and_wait_cancellation(deps, tmp_path):
    script = tmp_path / "wait.py"
    script.write_text("import time; time.sleep(60)\n")
    await deps.open()
    try:
        job = deps.registry.create_job("fixture")
        await deps.supervisor.start(job.job_id, [sys.executable, str(script)], cwd=tmp_path,
                                    wall_seconds=0.2, queued=True, queue_timeout_seconds=5, grace_seconds=0.1)
        result = await wait_job(deps, job.job_id, 0.25)
        assert result.job.state == State.QUEUED and result.receipt is None
        deps.supervisor.mark_admitted(job.job_id)
        result = await wait_job(deps, job.job_id, 5)
        assert result.receipt.queue_seconds >= 0.25
        assert result.receipt.held_seconds >= 0.2
        assert result.receipt.queue_seconds > 0
    finally:
        await deps.close()


async def test_cancel_unstarted_owned_job_only(deps):
    await deps.open()
    try:
        job = deps.registry.create_job("fixture")
        await deps.supervisor.cancel(job.job_id)
        assert deps.registry.get_job(job.job_id).state == State.CANCELLED
        with pytest.raises(WorkbenchError):
            await deps.supervisor.cancel("job_" + "0" * 32)
    finally:
        await deps.close()


def test_registry_cross_process_concurrency(tmp_path):
    code = """
import json,sys
from pathlib import Path
from src.registry import Registry
r=Registry(Path(sys.argv[1])); r.open()
job=r.create_job('shared',idempotency_key='exact',request={'value':1})
for n in range(30):
    r.append_event(job.job_id,'sample',{'worker':sys.argv[2],'n':n})
print(job.job_id)
"""
    state = tmp_path / "shared"
    children = [subprocess.Popen([sys.executable, "-c", code, str(state), str(n)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True) for n in range(2)]
    results = [p.communicate(timeout=15) for p in children]
    assert all(p.returncode == 0 for p in children), results
    assert results[0][0] == results[1][0]
    from src.registry import Registry
    registry = Registry(state)
    registry.open()
    job_id = results[0][0].strip()
    page = registry.events(job_id, limit=100)
    assert len(page.events) == 61
    assert len({e.cursor for e in page.events}) == 61
    assert registry.list_jobs().jobs[0].job_id == job_id
    registry.close()
