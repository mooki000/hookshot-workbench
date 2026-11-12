import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from src.models import Receipt, State, utc_now
from src.registry import Registry
from src.registry.process import identity, same_process
from src.tools.status import wait_job


async def test_async_operation_cleanup_and_run_receipt(deps):
    await deps.open()
    closed = []
    deps.add_cleanup(lambda: cleanup(closed))
    try:
        run = deps.registry.create_run()
        job = deps.registry.create_job("fixture")
        deps.registry.update_job(job.job_id, run_ids=[run.run_id])
        async def operation():
            await asyncio.Event().wait()
        async def cancel():
            closed.append("cancel")
            deps.registry.finish(job.job_id, State.CANCELLED,
                Receipt(job_id=job.job_id, observed_at=utc_now(), exit_code=None, stop_reason="cancelled",
                        process_exited=True, descendants_exited=True, queue_seconds=1, held_seconds=2))
        await deps.supervisor.start_operation(job.job_id, operation, cancel)
        await deps.supervisor.cancel(job.job_id)
        result = await wait_job(deps, job.job_id, 5)
        assert result.job.state == State.CANCELLED and closed == ["cancel"]
        finished = deps.registry.get_run(run.run_id)
        assert finished.state == State.CANCELLED and finished.gate.held_seconds == 2
    finally:
        await deps.close()
    assert closed == ["cancel", "close"]


async def cleanup(closed):
    closed.append("close")


async def test_missing_operation_receipt_is_not_success(deps):
    await deps.open()
    try:
        job = deps.registry.create_job("fixture")
        async def operation():
            return None
        await deps.supervisor.start_operation(job.job_id, operation)
        result = await wait_job(deps, job.job_id, 5)
        assert result.job.state == State.INTERRUPTED
        assert result.job.error.code == "uncertain_outcome"
    finally:
        await deps.close()


async def test_adapter_gets_stop_before_its_child(deps, tmp_path):
    script = tmp_path / "adapter.py"
    record = tmp_path / "ordered.txt"
    ready = tmp_path / "ready"
    script.write_text(
        "import os,signal,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"open({str(ready)!r},'w').write('ready')\n"
        "def stop(signum,frame):\n"
        f"    with open({str(record)!r},'w') as f: f.write('child_alive='+str(child.poll() is None))\n"
        "    child.terminate(); child.wait(); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "while True: time.sleep(.01)\n")
    await deps.open()
    try:
        job = deps.registry.create_job("fixture")
        await deps.supervisor.start(job.job_id, [sys.executable, str(script)], cwd=tmp_path,
                                    wall_seconds=3, cancellation="adapter", grace_seconds=1)
        deadline = asyncio.get_running_loop().time() + 2
        while not ready.exists():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.01)
        await deps.supervisor.cancel(job.job_id)
        result = await wait_job(deps, job.job_id, 5)
        assert record.read_text() == "child_alive=True"
        assert result.job.state == State.CANCELLED and result.receipt.descendants_exited
    finally:
        await deps.close()


async def test_parent_death_leaves_reconcilable_receipt(tmp_path):
    state = tmp_path / "state"
    child_script = tmp_path / "child.py"
    child_script.write_text("import time; time.sleep(60)\n")
    code = """
import asyncio,os,sys
from pathlib import Path
from src.registry import Registry
from src.registry.supervisor import Supervisor
async def run():
    r=Registry(Path(sys.argv[1])); r.open(); s=Supervisor(r)
    j=r.create_job('fixture')
    await s.start(j.job_id,[sys.executable,sys.argv[2]],cwd=Path(sys.argv[1]).parent,wall_seconds=2,grace_seconds=.1)
    while not (s.job_directory(j.job_id)/'ready').exists(): await asyncio.sleep(.01)
    print(j.job_id,flush=True)
    os._exit(0)
asyncio.run(run())
"""
    launcher = await asyncio.create_subprocess_exec(sys.executable, "-c", code, str(state), str(child_script),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(launcher.communicate(), 5)
    assert launcher.returncode == 0, err.decode()
    job_id = out.decode().strip()
    receipt_path = state / "workers" / job_id / "receipt.json"
    deadline = asyncio.get_running_loop().time() + 5
    while not receipt_path.exists():
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(.02)
    from src.registry.supervisor import Supervisor
    registry = Registry(state)
    registry.open()
    supervisor = Supervisor(registry)
    while same_process(registry.process(job_id)[0]):
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(.01)
    result = supervisor.reconcile(job_id)
    assert result.state == State.CANCELLED
    assert registry.receipt(job_id).descendants_exited
    registry.close()


def test_pid_birth_identity_not_pid_only():
    own = identity(os.getpid())
    assert own is not None and same_process(own)
    wrong = own.model_copy(update={"start_ticks": own.start_ticks + 1})
    assert not same_process(wrong)


def test_bootstrap_disables_cwd_dotenv(tmp_path):
    (tmp_path / ".env").write_text("FASTMCP_LOG_LEVEL=not-a-valid-level\n")
    result = subprocess.run([sys.executable, "-c", "import src.main; print('loaded')"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and result.stdout.strip() == "loaded"
