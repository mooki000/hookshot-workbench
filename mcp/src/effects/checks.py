"""Planning and owned CPU submissions. No game, SDK setup or public mutation."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import uuid
from src.errors import WorkbenchError
from src.models import Build, Producer, Receipt, State, TERMINAL, utc_now
from src.models.checks import Plan, PlanResult
from src.policy import child_environment
from src.effects.checks_common import (catalogue, capture, checkout, digest, dry_run, file_hash,
    git, json_read, prerequisite, safe_relative)


def area(deps):
    root = deps.config.state_root / "checks"
    deps.paths.output(root) if not root.exists() else deps.paths.read(root)
    root.mkdir(mode=0o700, exist_ok=True)
    return root


@contextmanager
def locked(deps, name):
    root = area(deps)
    path = root / (name + ".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def write_new(deps, path, value):
    fd = deps.paths.reserve_file(path)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def snapshot(deps, cat, root, commit, destination):
    deps.paths.output(destination)
    code, _ = capture([cat.git, "-c", "core.hooksPath=/dev/null", "clone", "--shared", "--no-checkout",
                       "--no-hardlinks", "--", root, destination], cwd=root,
                       env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"})
    if code:
        raise deps.error("backend_failed", "checks_snapshot")
    git(cat, destination, "-c", "core.hooksPath=/dev/null", "checkout", "--detach", commit)
    # No source-controlled symlink may escape the owned checkout, including into protected roots.
    for path in destination.rglob("*"):
        if path.is_symlink():
            target = deps.paths.read(path)
            if not target.is_relative_to(destination):
                raise deps.error("refused_by_policy", "checks_snapshot")
    return destination


def profile(deps, cat, profile_id, *, build=False):
    value = cat.profiles.get(profile_id)
    if value is None:
        raise deps.error("not_found", "checks_profile")
    prerequisite(deps, value.cpm, directory=True)
    python = prerequisite(deps, deps.config.dependency_python)
    inputs = {}
    for name, path in value.profile_inputs.items():
        relative = safe_relative(name)
        if relative.parts[0] != "work":
            raise deps.error("refused_by_policy", "checks_profile")
        inputs[name] = file_hash(prerequisite(deps, path))
    identity = {"profile": value.model_dump(mode="json"), "inputs": inputs, "python": file_hash(python)}
    if build:
        exefs = prerequisite(deps, value.exefs, directory=True)
        prerequisite(deps, value.romfs, directory=True)
        files = list(exefs.rglob("*"))
        if len(files) > 10000:
            raise deps.error("unavailable", "checks_build_inputs")
        identity["exefs"] = {str(p.relative_to(exefs)): file_hash(deps.paths.read(p)) for p in files if p.is_file()}
        if any(p.is_symlink() for p in files):
            raise deps.error("refused_by_policy", "checks_build_inputs")
    return value, digest(identity)


def lift_group(cat, root, commit):
    """The regression builder's deliberately conservative grouping, not an object key."""
    tree = git(cat, root, "ls-tree", "-r", commit, "--", "nx2", "third_party/a64-kit").splitlines()
    tree = [row for row in tree if not row.endswith("\tnx2/build.py")]
    names = set()
    host = git(cat, root, "ls-tree", "-r", "--name-only", commit, "--", "host").splitlines()
    for name in host:
        if name.endswith(".c"):
            source = git(cat, root, "show", commit + ":" + name)
            names.update(re.findall(r"^void (imp_\w+)\(Ctx", source, re.M))
            names.update("imp_" + n for n in re.findall(r"^HLE\((\w+)\)", source, re.M))
    return digest({"tree": tree, "hle": sorted(names)})


def copy_tree(deps, cat, source, destination):
    source = deps.paths.read(source)
    deps.paths.output(destination)
    for path in source.rglob("*"):
        if path.is_symlink():
            raise deps.error("refused_by_policy", "checks_donor")
    code, _ = capture([cat.copy_program, "-a", "--reflink=always", "--", source, destination], cwd=source, timeout=120)
    if code:
        raise deps.error("backend_failed", "checks_copy")


def seed_profile(deps, prof, source):
    for name, path in prof.profile_inputs.items():
        target = source / str(safe_relative(name))
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = deps.paths.reserve_file(target)
        with os.fdopen(fd, "wb") as out, deps.paths.read(path).open("rb") as src:
            while chunk := src.read(1024 * 1024):
                out.write(chunk)


def build_env(deps, prof, root, source):
    env = child_environment(root)
    env.update(RX_CPM=str(prof.cpm), RX_RELEASE="1", OOT_WORK=str(source / "work/build"),
        RX_PGO=str(source / "work/build/hookshot_pieces.prof"),
        HOOKSHOT_LIFT_JOBS=str(prof.lift_jobs), HOOKSHOT_KEEP_BUILD_FILES="1")
    return env


def donor_record(deps, donor_id):
    run = deps.registry.get_run(donor_id)
    if run.state != State.COMPLETED or not run.build.source_stamp or not run.build.binary_hashes:
        raise deps.error("refused_by_policy", "checks_donor")
    record = None
    for aid in run.artifacts:
        artifact = deps.registry.get_artifact(aid)
        if artifact.producer.tool == "build_start" and artifact.mime == "application/json" and artifact.complete:
            path = deps.registry.artifact_path(aid, deps.paths)
            if file_hash(path) != artifact.sha256:
                raise deps.error("refused_by_policy", "checks_donor")
            candidate = json_read(path)
            if candidate.get("kind") == "build_manifest":
                record = candidate
    if record is None:
        raise deps.error("unavailable", "checks_donor")
    receipt = deps.registry.receipt(record["job_id"])
    if receipt is None or receipt.exit_code != 0 or not receipt.descendants_exited:
        raise deps.error("refused_by_policy", "checks_donor")
    for name, expected in run.build.binary_hashes.items():
        if file_hash(deps.paths.read(Path(record["output"]) / str(safe_relative(name)))) != expected:
            raise deps.error("refused_by_policy", "checks_donor")
    if tree_identity(deps, Path(record["work"])) != record["work_identity"]:
        raise deps.error("refused_by_policy", "checks_donor")
    return record


def tree_identity(deps, root):
    files = []
    for path in sorted(deps.paths.read(root).rglob("*")):
        if path.is_symlink():
            raise deps.error("refused_by_policy", "checks_donor")
        if path.is_file():
            files.append((str(path.relative_to(root)), file_hash(deps.paths.read(path))))
    return digest(files)


def build_plan_impl(deps, repo_id, commit, profile_id, donor_id=None):
    cat = catalogue(deps)
    root = checkout(deps, cat, repo_id, commit)
    prof, identity = profile(deps, cat, profile_id, build=True)
    for path in (cat.git, cat.copy_program, deps.config.build_gate, cat.flock):
        prerequisite(deps, path)
    if cat.full_build_lock is None:
        raise deps.error("unavailable", "build_plan")
    # Check the lock's containment without taking it or creating it during planning.
    deps.paths.read(cat.full_build_lock) if cat.full_build_lock.exists() else deps.paths.output(cat.full_build_lock)
    group = lift_group(cat, root, commit)
    folder = area(deps) / ("plan_" + uuid.uuid4().hex)
    deps.paths.output(folder).mkdir(mode=0o700)
    source = snapshot(deps, cat, root, commit, folder / "source")
    donor, changes, evidence = None, [], ["conservative_regression_lift_group"]
    if donor_id:
        try:
            donor = donor_record(deps, donor_id)
            changes = git(cat, root, "diff", "--name-only", donor["commit"], commit).splitlines()
            # The fast path deploys binaries only. Shipped assets and build/packaging
            # changes require a full build even when the translator group matches.
            allowed = all(name.startswith(("host/", "gpu/")) and Path(name).suffix in
                          {".c", ".cc", ".cpp", ".h", ".hpp", ".inc"} for name in changes)
            if donor["profile_identity"] != identity or donor["lift_group"] != group or not allowed:
                donor = None
                evidence.append("donor_input_or_asset_mismatch_full_build")
        except WorkbenchError:
            donor = None
            evidence.append("donor_unverified_full_build")
    if donor:
        copy_tree(deps, cat, Path(donor["source"]) / "work", source / "work")
        evidence.append("completed_donor_receipt_and_content_hashes_verified")
    else:
        seed_profile(deps, prof, source)
    (source / "work/build").mkdir(parents=True, exist_ok=True)
    code, text = capture([deps.config.dependency_python, "-I", "-B", "nx2/build.py", "--standalone", "--dry-run"],
        cwd=source, env=build_env(deps, prof, folder, source), timeout=120)
    observed = dry_run(text)
    if donor and (code or observed["missing"] != 0 or any(observed[k] is None or observed[k] != donor["object_keys"].get(k) for k in ("headers", "flags", "hot_flags"))):
        donor = None
        evidence.append("dry_run_identity_mismatch_full_build")
    mode = "reuse" if donor else "full"
    evidence.append("dry_run_observed" if observed["missing"] is not None else "dry_run_unavailable_full_build")
    data = dict(repo_id=repo_id, commit=commit, profile_id=profile_id, mode=mode,
        donor_id=donor_id if donor else None, profile_identity=identity, lift_group=group,
        compatibility_evidence=evidence, changed_assets=changes[:100], changed_assets_truncated=len(changes) > 100,
        required_gates=["build"] + (["full_build_lock"] if mode == "full" else []), dry_run=observed)
    plan = Plan(**data, plan_hash=digest(data))
    path = folder / "plan.json"
    write_new(deps, path, {"plan": plan.model_dump(), "source": str(source), "catalogue_hash": digest(cat.model_dump(mode="json"))})
    artifact = deps.registry.add_artifact(path, guards=deps.paths, mime="application/json",
        producer=Producer(tool="build_plan", source_commit=commit), complete=True)
    return PlanResult(plan=plan, artifact=artifact)


def load_plan(deps, cat, plan_id):
    artifact = deps.registry.get_artifact(plan_id)
    if artifact.producer.tool != "build_plan" or not artifact.complete:
        raise deps.error("refused_by_policy", "build_start")
    path = deps.registry.artifact_path(plan_id, deps.paths)
    if file_hash(path) != artifact.sha256:
        raise deps.error("refused_by_policy", "build_start")
    raw = json_read(path)
    plan = Plan.model_validate(raw["plan"])
    if plan.plan_hash not in cat.approved_plans:
        raise deps.error("refused_by_policy", "build_start")
    return plan


async def submit_impl(deps, operation, request, idempotency_key):
    cat = catalogue(deps)
    if operation == "build_start":
        plan = load_plan(deps, cat, request["plan_id"])
        request = {**request, "repo_id": plan.repo_id, "commit": plan.commit, "profile_id": plan.profile_id,
                   "plan": plan.model_dump()}
    root = checkout(deps, cat, request["repo_id"], request["commit"])
    prof = cat.profiles.get(request.get("profile_id", "default"))
    if prof is None:
        raise deps.error("not_found", "checks_profile")
    if request.get("compare_with_base"):
        checkout(deps, cat, request["repo_id"], request["compare_with_base"])
    if request.get("base"):
        checkout(deps, cat, request["repo_id"], request["base"])
    prerequisites = [cat.git, deps.config.dependency_python]
    if operation == "build_start":
        prerequisites += [deps.config.build_gate, cat.flock, cat.copy_program]
        if cat.full_build_lock is None:
            raise deps.error("unavailable", operation)
        deps.paths.read(cat.full_build_lock) if cat.full_build_lock.exists() else deps.paths.output(cat.full_build_lock)
    elif operation in ("checks_source", "checks_windows_syntax"):
        prerequisites += [cat.idle, cat.nocore]
        if operation == "checks_windows_syntax":
            prerequisites.append(cat.windows_check)
            prerequisite(deps, cat.windows_sysroot, directory=True)
    elif operation == "checks_pr":
        prerequisites += [cat.pr_check]
        if cat.pr_check_checkout is None or deps.paths.read(cat.pr_check_checkout) != root:
            raise deps.error("unavailable", "checks_pr_repository_binding")
        if git(cat, root, "rev-parse", request["branch"] + "^{commit}") != request["commit"]:
            raise deps.error("refused_by_policy", "checks_pr_head")
        artifact = deps.registry.get_artifact(request["title_artifact"])
        if artifact.mime != "text/plain" or not artifact.complete or artifact.bytes is None or artifact.bytes > 512:
            raise deps.error("invalid_argument", "checks_pr_title")
        path = deps.registry.artifact_path(artifact.id, deps.paths)
        if file_hash(path) != artifact.sha256:
            raise deps.error("refused_by_policy", "checks_pr_title")
    for path in prerequisites:
        prerequisite(deps, path)
    lane = "build" if operation == "build_start" else "idle"
    plan_hash = request.get("plan", {}).get("plan_hash") or digest(request)
    policy = deps.policy.authorize(lane=lane, resource_class="ordinary", wall_seconds=prof.wall_seconds, plan_hash=plan_hash)
    # A short file lock makes deduplication and the full-build reservation atomic across servers.
    with locked(deps, "submission"):
        job = deps.registry.create_job(operation, idempotency_key=idempotency_key, request=request)
        request_path = area(deps) / (job.job_id + ".json")
        if request_path.exists() or job.state in TERMINAL or deps.registry.process(job.job_id)[0] is not None:
            return deps.registry.get_job(job.job_id)
        if operation == "build_start" and request["plan"]["mode"] == "full":
            active = area(deps) / "full-build.json"
            if active.exists():
                previous = deps.registry.get_job(json_read(active)["job_id"])
                if previous.state not in TERMINAL:
                    deps.registry.finish(job.job_id, State.FAILED, Receipt(job_id=job.job_id, observed_at=utc_now(),
                        exit_code=None, stop_reason="full_build_busy", process_exited=True, descendants_exited=True,
                        queue_seconds=None, held_seconds=None), error=deps.error("busy", operation).envelope)
                    raise deps.error("busy", "build_start")
            # Internal reservation metadata is replaced under the lock, never evidence.
            active.write_text(json.dumps({"job_id": job.job_id}))
        run_ids = []
        if operation == "build_start":
            run = deps.registry.create_run(state=State.QUEUED, build=Build(commit=request["commit"], profile_id=request["profile_id"]),
                config_hash=plan_hash, policy_revision=policy.revision)
            deps.registry.own_run(job.job_id, run.run_id)
            run_ids = [run.run_id]
            deps.registry.update_job(job.job_id, run_ids=run_ids)
        worker_config = {key: getattr(deps.config, key) for key in ("state_root", "build_gate", "dependency_python",
            "checks_catalogue", "read_roots", "write_roots", "protected_roots")}
        payload = {"operation": operation, "request": request, "job_id": job.job_id, "run_ids": run_ids,
                   "config": {key: str(value) if isinstance(value, Path) else [str(p) for p in value] if key.endswith("roots") else value
                              for key, value in worker_config.items()},
                   "plan_hash": plan_hash, "lane": lane}
        write_new(deps, request_path, payload)
    # Installed trusted worker only; it will re-read policy inside the real gate before execution.
    try:
        return await deps.supervisor.start(job.job_id,
            [sys.executable, "-I", "-m", "src.effects.checks_worker", str(request_path)],
            cwd=root, wall_seconds=prof.wall_seconds, queue_timeout_seconds=prof.queue_seconds, queued=True)
    except Exception:
        # The durable request/attempt is retained. A retry with this key never replays it.
        raise deps.error("uncertain_outcome", operation) from None
