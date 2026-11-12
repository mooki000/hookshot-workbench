"""Private fixed-operation worker, reachable only through server-owned request files."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from src.config import Config
from src.deps import Deps
from src.errors import WorkbenchError
from src.models import utc_now
from src.models.knowledge import KnowledgeConfig
from src.effects.knowledge_common import config, digest
from src.effects.knowledge_github import github_thread_read_impl
from src.effects.knowledge_jobs import persist_result
from src.effects.knowledge_loop import refresh_impl


async def watch(deps, job_id, request):
    deadline = asyncio.get_running_loop().time() + request["timeout_seconds"]
    hashes = dict(request["prior_hashes"])
    changes = []
    change_count = 0
    saved = 0
    for poll in range(1000):
        if asyncio.get_running_loop().time() >= deadline:
            break
        deps.registry.update_job(job_id, phase="observing_threads", completed_steps=poll,
            progress_text="Reading allowlisted thread snapshots.")
        observed = dict(hashes)
        batch = []
        for ref in request["threads"]:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(remaining):
                    snapshot = await github_thread_read_impl(deps, **ref, limit=1, owned_group=True)
            except TimeoutError:
                break
            key = f"{ref['repository']}#{ref['number']}"
            if not snapshot.pages_complete or snapshot.consistent is not True:
                deps.registry.append_event(job_id, "snapshot_incomplete", {**ref, "pages_complete": snapshot.pages_complete,
                    "consistent": snapshot.consistent})
                continue
            observed[key] = snapshot.snapshot_hash
            if key in hashes and hashes[key] != snapshot.snapshot_hash:
                change = {**ref, "previous_hash": hashes[key], "snapshot_hash": snapshot.snapshot_hash,
                    "head": snapshot.head, "updated_at": snapshot.updated_at}
                batch.append(change)
                deps.registry.append_event(job_id, "thread_changed", change)
        if saved == 0 or observed != hashes:
            hashes = observed
            change_count += len(batch)
            changes = (changes + batch)[-100:]
            persist_result(deps, job_id, "github_watch", {"threads": request["threads"], "hashes": hashes,
                "changes": changes, "changes_total": change_count, "changes_truncated": change_count > len(changes),
                "observed_at": utc_now(), "pages_complete": len(hashes) == len(request["threads"])}, saved)
            saved += 1
        deps.registry.append_event(job_id, "watch_observed", {"poll": poll + 1, "complete_threads": len(observed), "changes": len(batch)})
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(config(deps).watch_interval_seconds, remaining))
    deps.registry.update_job(job_id, phase="watch_finished", progress_text="Watcher deadline or bounded observation budget reached; retained cursors are unchanged by cancellation.")


async def run(path):
    # The parent reserves this private immutable file; there is no MCP path parameter.
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        return 1
    payload = json.loads(raw)
    settings = payload["config"]
    settings = {key: tuple(Path(p) for p in value) if isinstance(value, list) else Path(value) if value is not None else None
        for key, value in settings.items()}
    base = Config(**settings)
    values = vars(base).copy()
    values["knowledge"] = KnowledgeConfig.model_validate(payload["knowledge"])
    deps = Deps(SimpleNamespace(**values))
    deps.registry.open()
    operation, job_id = payload["operation"], payload["job_id"]
    try:
        deps.registry.append_event(job_id, "knowledge_started", {"operation": operation})
        if operation == "github_watch":
            await watch(deps, job_id, payload["request"])
        elif operation == "prloop_refresh":
            deps.registry.update_job(job_id, phase="scanning", progress_text="Scanning existing loop state under its own coordinator lock.")
            result = refresh_impl(deps, payload["request"]["snapshot_id"])
            persist_result(deps, job_id, operation, result)
            deps.registry.update_job(job_id, phase="snapshot_retained", completed_steps=1)
        else:
            raise deps.error("refused_by_policy", "knowledge_worker")
        return 0
    except Exception as error:
        code = error.envelope.code if isinstance(error, WorkbenchError) else "backend_failed"
        if operation == "prloop_refresh" and code not in {"busy", "refused_by_policy", "unavailable", "not_found"}:
            code = "uncertain_outcome"
        envelope = deps.error(code, operation).envelope
        deps.registry.update_job(job_id, error=envelope)
        deps.registry.append_event(job_id, "knowledge_error", envelope.model_dump())
        return 1
    finally:
        deps.registry.close()


def main():
    try:
        code = asyncio.run(run(Path(sys.argv[1])))
    except Exception:
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
