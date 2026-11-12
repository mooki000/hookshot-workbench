import json
import os
from dataclasses import replace
from pathlib import Path
import pytest
from fastmcp import Client
from src.deps import Deps
from src.effects.gates import GateReader
from src.main import build_server

NOW = 2_000_000_000.0


@pytest.fixture
def gate_fixture(config, tmp_path):
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("test-boot")
    (proc / "stat").write_text("btime 1000\n")
    (proc / "locks").write_text("")
    paths = {}
    scripts = {}
    for name in ("gpu", "intel", "build"):
        paths[name] = tmp_path / (name + "-slots")
        paths[name].mkdir()
        scripts[name] = tmp_path / ("test-" + name + "-gate")
        scripts[name].write_text(f"dir=${{TEST_SLOT_DIR:-{paths[name]}}}\ngpu=${{TEST_GPU_DIR:-{tmp_path / 'gpu-slots'}}}\nswitch=$dir/gpu-slots\n")
        for slot in (("0", "1") if name == "gpu" else ("0",) if name == "intel" else ("0", "1", "s2", "s3")):
            (paths[name] / slot).touch()
    (paths["gpu"] / "queue").mkdir()
    (paths["gpu"] / "yielded").mkdir()
    (paths["gpu"] / "quiet").touch()
    (paths["gpu"] / "last-kind").write_text("o\n")
    (paths["gpu"] / "gpu-slots").write_text("2")
    config = replace(config, gpu_gate=scripts["gpu"], intel_gate=scripts["intel"], build_gate=scripts["build"])
    def process(pid, device="gpu", flags=(), zombie=False, born=NOW - 500):
        folder = proc / str(pid)
        folder.mkdir(exist_ok=True)
        fields = ["0"] * 20
        fields[0] = "Z" if zombie else "S"
        fields[1:4] = ["1", str(pid), str(pid)]
        fields[19] = str(int((born - 1000) * os.sysconf("SC_CLK_TCK")))
        (folder / "stat").write_text(f"{pid} (fixture) " + " ".join(fields))
        (folder / "cmdline").write_text("\0".join(["bash", str(scripts[device]), *flags]))
    def ticket(pid, age=60, priority=0, yielded=False):
        stamp = int((NOW - age - priority * 86400) * 1e9)
        path = paths["gpu"] / ("yielded" if yielded else "queue") / f"{stamp}.{pid}"
        path.write_text("2\n")
        return path.name
    def lock(device, slot, pid, mode="WRITE", blocked=False):
        info = (paths[device] / slot).stat()
        key = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
        with (proc / "locks").open("a") as stream:
            stream.write(f"1: {'-> ' if blocked else ''}FLOCK ADVISORY {mode} {pid} {key} 0 EOF\n")
    return config, GateReader(config, proc_root=proc, clock=lambda: NOW), paths, proc, process, ticket, lock


def test_dead_ticket_and_no_holder(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    ticket(99)
    process(98, zombie=True)
    ticket(98)
    (paths["gpu"] / "holder.0").write_text("99\n")
    before = sorted(str(p.relative_to(paths["gpu"])) for p in paths["gpu"].rglob("*"))
    result = reader.snapshot("gpu").devices[0]
    assert result.holders == result.waiters == []
    assert result.stale_tickets == 2 and result.stale_holder_files == 1
    assert result.position_or_null is None and result.eta.range_seconds is None
    assert "unknown" in result.eta.basis
    assert before == sorted(str(p.relative_to(paths["gpu"])) for p in paths["gpu"].rglob("*"))
    assert len(list((paths["gpu"] / "queue").iterdir())) == 2


def test_priority_age_and_alternation(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    process(101, flags=("--priority",))
    process(102)
    process(103, flags=("--priority",))
    ticket(101, age=90, priority=1)
    ticket(102, age=120)
    ticket(103, age=30, priority=1)
    snapshot = reader.snapshot("gpu", request_pid=101)
    assert [w.pid for w in snapshot.devices[0].waiters] == [101, 102, 103]
    assert snapshot.devices[0].waiters[0].age_seconds == 90
    assert snapshot.devices[0].position_or_null == 1
    (paths["gpu"] / "last-kind").write_text("p ticket\n")
    snapshot = reader.snapshot("gpu", request_pid=101)
    assert [w.pid for w in snapshot.devices[0].waiters] == [102, 101, 103]
    assert snapshot.devices[0].position_or_null == 2
    assert all(w.observed_at == snapshot.observed_at for w in snapshot.devices[0].waiters)


def test_yielded_and_first_exclusive(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    process(101, flags=("--priority",))
    process(102)
    process(103, flags=("--first", "--exclusive"))
    ticket(101, priority=1, yielded=True)
    ticket(102)
    ticket(103, priority=2)
    (paths["gpu"] / "last-kind").write_text("p ticket\n")
    result = reader.snapshot("gpu", request_pid=101).devices[0]
    assert [w.pid for w in result.yielded] == [101]
    assert result.yielded[0].yielded
    assert result.position_or_null == 3
    assert result.waiters[0].pid == 103
    (paths["gpu"] / "last-kind").write_text("o ticket\n")
    result = reader.snapshot("gpu", request_pid=101).devices[0]
    assert result.position_or_null == 2  # First-exclusive does not rewrite last-kind.


def test_exclusive_holder_and_quiet_markers(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    process(101, flags=("--exclusive",))
    process(102, flags=("--exclusive",))
    for slot in ("0", "1"):
        lock("gpu", slot, 101)
        holder = paths["gpu"] / ("holder." + slot)
        holder.write_text("101\n")
        os.utime(holder, (NOW - 30, NOW - 30))
    lock("gpu", "quiet", 101)
    (paths["gpu"] / "quiet-wanted.102").touch()
    (paths["gpu"] / "quiet-wanted.999").touch()
    result = reader.snapshot()
    assert result.quiet.exclusive and result.quiet.wanted_pids == [102]
    assert result.quiet.stale_markers == 1
    assert all(h.resource_class == "exclusive" and h.age_seconds == 30 for h in result.devices[0].holders)
    assert "exclusive_quiet_lock" in result.devices[1].blocking_reasons


def test_build_intel_have_no_invented_fifo(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    process(201, "build")
    process(202, "intel")
    lock("build", "s2", 201)
    lock("intel", "0", 202, blocked=True)
    lock("gpu", "quiet", 201, mode="READ")
    result = reader.snapshot(request_pid=202)
    assert result.devices[1].waiters[0].age_seconds is None
    assert result.devices[1].position_or_null is None
    assert result.devices[2].holders[0].slot == "s2"
    assert result.devices[2].holders[0].age_seconds is None
    assert result.quiet.exclusive is False


async def test_queue_through_actual_registration(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    process(101)
    ticket(101)
    deps = Deps(config, gates=reader)
    async with Client(build_server(deps=deps)) as client:
        job = deps.registry.create_job("fixture")
        deps.registry.append_event(job.job_id, "gate_request", {"pid": 101, "device": "gpu"})
        result = await client.call_tool("queue_status", {"device": "gpu", "job_id": job.job_id})
        assert result.data.devices[0].position_or_null == 1
        assert result.data.atomic is False


def test_missing_state_and_unreadable_locks(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    (proc / "locks").unlink()
    assert reader.snapshot().devices[0].reason == "proc_locks_unreadable"
    assert reader.snapshot().quiet.exclusive is None
    absent = GateReader(replace(config, gpu_gate=None), proc_root=proc, clock=lambda: NOW)
    assert absent.snapshot("gpu").devices[0].status == "unavailable"


def test_queue_pagination(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    for pid in range(100, 105):
        process(pid)
        ticket(pid, age=pid)
    page = reader.snapshot("gpu", cursor=0, limit=2).devices[0]
    assert len(page.waiters) == 2 and page.next_cursor == 2
    page2 = reader.snapshot("gpu", cursor=page.next_cursor, limit=2).devices[0]
    assert not {w.ticket for w in page.waiters} & {w.ticket for w in page2.waiters}


def test_reused_gate_pid_does_not_revive_old_ticket(gate_fixture):
    config, reader, paths, proc, process, ticket, lock = gate_fixture
    process(101, born=NOW - 10)
    ticket(101, age=60)
    result = reader.snapshot("gpu").devices[0]
    assert result.stale_tickets == 1 and result.waiters == []
